import os
import json
import yaml
import fitz
import asyncio
import pandas as pd
import streamlit as st
import re

from pathlib import Path
from dotenv import load_dotenv

from langchain_community.vectorstores import FAISS
from langchain_core.runnables import RunnablePassthrough
from langchain.chains import RetrievalQA, create_retrieval_chain
from langchain_core.prompts import ChatPromptTemplate
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain.output_parsers import OutputFixingParser
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# GraphRAG imports
import graphrag.api as api
from graphrag.cli.initialize import initialize_project_at
from graphrag.index.typing import PipelineRunResult
from graphrag.config.create_graphrag_config import create_graphrag_config
from graphrag.query.llm.oai.chat_openai import ChatOpenAI
from graphrag.query.llm.oai.typing import OpenaiApiType
from graphrag.query.indexer_adapters import (
    read_indexer_communities,
    read_indexer_entities,
    read_indexer_reports,
)
from graphrag.query.structured_search.global_search.community_context import (
    GlobalCommunityContext,
)
from graphrag.query.structured_search.global_search.search import GlobalSearch

from graphrag.query.context_builder.entity_extraction import EntityVectorStoreKey
from graphrag.query.indexer_adapters import (
    read_indexer_covariates,
    read_indexer_relationships,
    read_indexer_text_units,
)
from graphrag.query.llm.oai.embedding import OpenAIEmbedding
from graphrag.query.question_gen.local_gen import LocalQuestionGen
from graphrag.query.structured_search.local_search.mixed_context import (
    LocalSearchMixedContext,
)
from graphrag.query.structured_search.local_search.search import LocalSearch
from graphrag.vector_stores.lancedb import LanceDBVectorStore

from graphrag.config.init_content import INIT_DOTENV, INIT_YAML
from graphrag.prompts.index.claim_extraction import CLAIM_EXTRACTION_PROMPT
from graphrag.prompts.index.community_report import (
    COMMUNITY_REPORT_PROMPT,
)
from graphrag.prompts.index.entity_extraction import GRAPH_EXTRACTION_PROMPT
from graphrag.prompts.index.summarize_descriptions import SUMMARIZE_PROMPT
from graphrag.prompts.query.drift_search_system_prompt import DRIFT_LOCAL_SYSTEM_PROMPT
from graphrag.prompts.query.global_search_knowledge_system_prompt import (
    GENERAL_KNOWLEDGE_INSTRUCTION,
)
from graphrag.prompts.query.global_search_map_system_prompt import MAP_SYSTEM_PROMPT
from graphrag.prompts.query.global_search_reduce_system_prompt import (
    REDUCE_SYSTEM_PROMPT,
)
from graphrag.prompts.query.local_search_system_prompt import LOCAL_SEARCH_SYSTEM_PROMPT
from graphrag.prompts.query.question_gen_system_prompt import QUESTION_SYSTEM_PROMPT

from streamlit_float import *

from pipeline.api_handler import ApiHandler
from pipeline.api_handler import create_env_file
from pipeline.api_handler import ApiHandler, create_env_file
from pipeline.helper.index_files_saving import graphrag_index_files_check, graphrag_index_files_compress, graphrag_index_files_decompress
from pipeline.config import load_config
from pipeline.utils import (
    count_tokens,
    truncate_chat_history,
    truncate_document,
    get_llm,
    get_embedding_models,
    extract_images_from_pdf,
    extract_pdf_content_to_markdown,
    extract_pdf_content_to_markdown_via_api,
    create_searchable_chunks,
)
from pipeline.images_understanding import initialize_image_files


load_dotenv()
# Control whether to use Marker API or not. Only for local environment we skip Marker API.
SKIP_MARKER_API = True if os.getenv("ENVIRONMENT") == "local" else False
print(f"SKIP_MARKER_API: {SKIP_MARKER_API}")


def generate_embedding(_documents, _doc, pdf_path, embedding_folder):
    """
    Generate embeddings for the documents
    If the embeddings already exist, load them
    Otherwise, extract content to markdown via API or local PDF extraction
    Then, initialize image files and try to append image context to texts with error handling
    Create the vector store to use as the index
    Save the embeddings to the specified folder
    Generate and save document summary using the texts we created
    """
    config = load_config()
    para = config['llm']
    embeddings = get_embedding_models('default', para)

    # Define the default filenames used by FAISS when saving
    faiss_path = os.path.join(embedding_folder, "index.faiss")
    pkl_path = os.path.join(embedding_folder, "index.pkl")
    documents_summary_path = os.path.join(embedding_folder, "documents_summary.txt")

    # Check if all necessary files exist to load the embeddings
    if os.path.exists(faiss_path) and os.path.exists(pkl_path) and os.path.exists(documents_summary_path):
        # Load existing embeddings
        print("Loading existing embeddings...")
        db = FAISS.load_local(
            embedding_folder, embeddings, allow_dangerous_deserialization=True
        )
    else:
        try:
            # Extract content to markdown via API
            if not SKIP_MARKER_API:
                print("Marker API is enabled. Using Marker API to extract content to markdown.")
                markdown_dir = os.path.join(embedding_folder, "markdown")
                md_path, saved_images, md_document = extract_pdf_content_to_markdown_via_api(pdf_path, markdown_dir)
                st.session_state.md_document = md_document
            else:
                print("Marker API is disabled. Using local PDF extraction.")
                markdown_dir = os.path.join(embedding_folder, "markdown")
                md_path, saved_images, md_document = extract_pdf_content_to_markdown(pdf_path, markdown_dir)
                st.session_state.md_document = md_document
        except Exception as e:
            print(f"Error extracting content to markdown via API: {e}")
            # Use _doc to extract searchable content
            st.session_state.md_document = ""
            texts = []
            
            # Process each page in the PDF document
            for page_num in range(len(_doc)):
                page = _doc[page_num]
                # Get all text blocks that can be found via search
                text_blocks = []
                for block in page.get_text("blocks"):
                    text = block[4]  # The text content is at index 4
                    # Verify the text can be found via search
                    search_results = page.search_for(text.strip())
                    if search_results:
                        text_blocks.append(text)
                
                # Join the searchable text blocks
                page_content = "\n".join(text_blocks)
                st.session_state.md_document += page_content.strip() + "\n"
                texts.append(Document(
                    page_content=page_content,
                    metadata={"source": f"page_{page_num + 1}", "page": page_num + 1}
                ))

            # Save to markdown_dir
            markdown_dir = os.path.join(embedding_folder, "markdown")
            os.makedirs(markdown_dir, exist_ok=True)
            md_path = os.path.join(markdown_dir, "content.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(st.session_state.md_document)
            
            # Use the texts directly instead of splitting again
            print(f"Number of pages processed: {len(texts)}")
        else:
            # Split the documents into chunks when markdown extraction succeeded
            average_page_length = sum(len(doc.page_content) for doc in _documents) / len(_documents)
            chunk_size = int(average_page_length // 3)
            print(f"Average page length: {average_page_length}")
            print(f"Chunk size: {chunk_size}")
            print("Creating new embeddings...")
            texts = create_searchable_chunks(_doc, chunk_size)
            print(f"length of document chunks generated for get_response_source:{len(texts)}")

        # Initialize image files and try to append image context to texts with error handling
        try:
            markdown_dir = os.path.join(embedding_folder, "markdown")
            image_context_path, _ = initialize_image_files(markdown_dir)
            
            with open(image_context_path, "r") as f:
                image_context = json.load(f)
            
            # Only process image context if there are actual images
            if image_context:
                print(f"Found {len(image_context)} images with context")
                
                # Create a temporary FAISS index for similarity search
                temp_db = FAISS.from_documents(texts, embeddings)
                
                for image, context in image_context.items():
                    for c in context:
                        # Clean the context text for comparison
                        clean_context = c.replace(" <markdown>", "").strip()
                        
                        # Use similarity search to find the most relevant chunk
                        similar_chunks = temp_db.similarity_search_with_score(clean_context, k=1)
                        
                        if similar_chunks:
                            best_match_chunk, score = similar_chunks[0]
                            # Only use the page number if the similarity score is good enough
                            # (score is distance, so lower is better)
                            best_match_page = best_match_chunk.metadata.get("page", 0) if score < 1.0 else 0
                        else:
                            best_match_page = 0
                        
                        texts.append(Document(
                            page_content=c, 
                            metadata={
                                "source": image,
                                "page": best_match_page
                            }
                        ))

                        # # TEST
                        # print(f"for image {image}, found page {best_match_page}")
            else:
                print("No image context found to process")
        except Exception as e:
            print(f"Error processing image context: {e}")
            print("Continuing without image context...")

        # Create the vector store to use as the index
        db = FAISS.from_documents(texts, embeddings)
        # Save the embeddings to the specified folder
        db.save_local(embedding_folder)

        try:
            # Generate and save document summary using the texts we created
            generate_document_summary(texts, embedding_folder)
        except Exception as e:
            print(f"Error generating document summary: {e}")
            print("Continuing without document summary...")

    return


async def generate_GraphRAG_embedding(_documents, embedding_folder):
    GraphRAG_embedding_folder = os.path.join(embedding_folder, "GraphRAG/")
    create_final_community_reports_path = GraphRAG_embedding_folder + "output/create_final_community_reports.parquet"
    create_final_covariates_path = GraphRAG_embedding_folder + "output/create_final_covariates.parquet"
    create_final_documents_path = GraphRAG_embedding_folder + "output/create_final_documents.parquet"
    create_final_entities_path = GraphRAG_embedding_folder + "output/create_final_entities.parquet"
    create_final_nodes_path = GraphRAG_embedding_folder + "output/create_final_nodes.parquet"
    create_final_relationships_path = GraphRAG_embedding_folder + "output/create_final_relationships.parquet"
    create_final_text_units_path = GraphRAG_embedding_folder + "output/create_final_text_units.parquet"
    create_final_communities_path = GraphRAG_embedding_folder + "output/create_final_communities.parquet"
    lancedb_path = GraphRAG_embedding_folder + "output/lancedb/"
    path_list = [
        create_final_community_reports_path,
        create_final_covariates_path,
        create_final_documents_path,
        create_final_entities_path,
        create_final_nodes_path,
        create_final_relationships_path,
        create_final_text_units_path,
        create_final_communities_path,
        lancedb_path
    ]

    # Check if all necessary paths in path_list exist
    if all([os.path.exists(path) for path in path_list]):
        # Load existing embeddings
        print("All necessary index files exist. Loading existing knowledge graph embeddings...")
    else:
        # Create the GraphRAG embedding
        print("Creating new knowledge graph embeddings...")

        # Initialize the project
        create_env_file(GraphRAG_embedding_folder)
        try:
            """Initialize the project at the given path."""
            # Initialize the project
            path = GraphRAG_embedding_folder
            root = Path(path)
            if not root.exists():
                root.mkdir(parents=True, exist_ok=True)
            dotenv = root / ".env"
            if not dotenv.exists():
                with dotenv.open("wb") as file:
                    file.write(INIT_DOTENV.encode(encoding="utf-8", errors="strict"))
            prompts_dir = root / "prompts"
            if not prompts_dir.exists():
                prompts_dir.mkdir(parents=True, exist_ok=True)
            prompts = {
                "entity_extraction": GRAPH_EXTRACTION_PROMPT,
                "summarize_descriptions": SUMMARIZE_PROMPT,
                "claim_extraction": CLAIM_EXTRACTION_PROMPT,
                "community_report": COMMUNITY_REPORT_PROMPT,
                "drift_search_system_prompt": DRIFT_LOCAL_SYSTEM_PROMPT,
                "global_search_map_system_prompt": MAP_SYSTEM_PROMPT,
                "global_search_reduce_system_prompt": REDUCE_SYSTEM_PROMPT,
                "global_search_knowledge_system_prompt": GENERAL_KNOWLEDGE_INSTRUCTION,
                "local_search_system_prompt": LOCAL_SEARCH_SYSTEM_PROMPT,
                "question_gen_system_prompt": QUESTION_SYSTEM_PROMPT,
            }
            for name, content in prompts.items():
                prompt_file = prompts_dir / f"{name}.txt"
                if not prompt_file.exists():
                    with prompt_file.open("wb") as file:
                        file.write(content.encode(encoding="utf-8", errors="strict"))
        except Exception as e:
            print("Initialization error:", e)
        settings = yaml.safe_load(open("./pipeline/graphrag_settings.yaml"))
        graphrag_config = create_graphrag_config(
            values=settings, root_dir=GraphRAG_embedding_folder
        )

        try:
            await api.build_index(config=graphrag_config)
        except Exception as e:
            print("Index building error:", e)

    return


def generate_document_summary(_documents, embedding_folder):
    """
    Generate a comprehensive markdown-formatted summary of the documents using multiple LLM calls.
    Documents can come from either processed PDFs or markdown files.
    """
    config = load_config()
    para = config['llm']
    llm = get_llm(para["level"], para)  # Using advanced model for better quality

    # # TEST
    # print("Current model:", llm)

    # First try to get content from session state
    combined_content = ""
    if hasattr(st.session_state, 'md_document') and st.session_state.md_document:
        print("Using markdown content from session state...")
        combined_content = st.session_state.md_document
    
    # If no content in session state, fall back to document content
    if not combined_content and _documents:
        print("Using document content as source...")
        combined_content = "\n\n".join(doc.page_content for doc in _documents)
    
    if not combined_content:
        raise ValueError("No content available from either session state or documents")

    # First generate the take-home message
    takehome_prompt = """
    Provide a single, impactful sentence that captures the most important takeaway from this document.
    
    Guidelines:
    - Be extremely concise and specific
    - Focus on the main contribution or finding
    - Use bold for key terms
    - Keep it to one sentence
    - Add a relevant emoji at the start of bullet points or the first sentence
    - For inline formulas use single $ marks: $E = mc^2$
    - For block formulas use double $$ marks:
      $$
      F = ma (just an example, may not be a real formula in the doc)
      $$
    
    Document: {document}
    """
    takehome_prompt = ChatPromptTemplate.from_template(takehome_prompt)
    str_parser = StrOutputParser()
    takehome_chain = takehome_prompt | llm | str_parser
    takehome = takehome_chain.invoke({"document": truncate_document(combined_content)})

    # Topics extraction
    topics_prompt = """
    Identify only the most essential topics/sections from this document.
    Be extremely selective and concise - only include major components.
    
    Return format:
    {{"topics": ["topic1", "topic2", ...]}}
    
    Guidelines:
    - Include maximum 4-5 topics
    - Focus only on critical sections
    - Use short, descriptive names
    
    Document: {document}
    """
    topics_prompt = ChatPromptTemplate.from_template(topics_prompt)
    parser = JsonOutputParser()
    error_parser = OutputFixingParser.from_llm(parser=parser, llm=llm)
    topics_chain = topics_prompt | llm | error_parser
    topics_result = topics_chain.invoke({"document": truncate_document(combined_content)})

    try:
        topics = topics_result.get("topics", [])
    except AttributeError:
        print("Warning: Failed to get topics. Using default topics.")
        topics = ["Overview", "Methods", "Results", "Discussion"]

    # Generate overview
    overview_prompt = """
    Provide a clear and engaging overview using bullet points.

    Guidelines:
    - Use 3-4 concise bullet points
    - **Bold** for key terms
    - Each bullet point should be one short sentence
    - For inline formulas use single $ marks: $E = mc^2$
    - For block formulas use double $$ marks:
      $$
      F = ma (just an example, may not be a real formula in the doc)
      $$
    
    Document: {document}
    """
    overview_prompt = ChatPromptTemplate.from_template(overview_prompt)
    overview_chain = overview_prompt | llm | str_parser
    overview = overview_chain.invoke({"document": truncate_document(combined_content)})

    # Generate summaries for each topic
    summaries = []
    for topic in topics:
        topic_prompt = """
        Provide an engaging summary for the topic "{topic}" using bullet points.
        
        Guidelines:
        - Use 2-3 bullet points
        - Each bullet point should be one short sentence
        - **Bold** for key terms
        - Use simple, clear language
        - Include specific metrics only if crucial
        - For inline formulas use single $ marks: $E = mc^2$
        - For block formulas use double $$ marks:
          $$
          F = ma (just an example, may not be a real formula in the doc)
          $$
        
        Document: {document}
        """
        topic_prompt = ChatPromptTemplate.from_template(topic_prompt)
        topic_chain = topic_prompt | llm | str_parser
        topic_summary = topic_chain.invoke({
            "topic": topic,
            "document": truncate_document(combined_content)
        })
        summaries.append((topic, topic_summary))

    # Combine everything into markdown format with welcome message and take-home message
    markdown_summary = f"""### 👋 Welcome to DeepTutor! 

I'm your AI tutor 🤖 ready to help you understand this document.

### 💡 Key Takeaway
{takehome}

### 📚 Document Overview
{overview}

"""
    
    # Add emojis for common topic titles
    topic_emojis = {
        "introduction": "📖",
        "overview": "🔎",
        "background": "📚",
        "methods": "🔬",
        "methodology": "🔬", 
        "results": "📊",
        "discussion": "💭",
        "conclusion": "🎯",
        "future work": "🔮",
        "implementation": "⚙️",
        "evaluation": "📈",
        "analysis": "🔍",
        "design": "✏️",
        "architecture": "🏗️",
        "experiments": "🧪",
        "related work": "🔗",
        "motivation": "💪",
        "approach": "🎯",
        "system": "🖥️",
        "framework": "🔧",
        "model": "🤖",
        "data": "📊",
        "algorithm": "⚡",
        "performance": "⚡",
        "limitations": "⚠️",
        "applications": "💡",
        "default": "📌" # Default emoji for topics not in the mapping
    }

    for topic, summary in summaries:
        # Get emoji based on topic, defaulting to 📌 if not found
        topic_lower = topic.lower()
        emoji = next((v for k, v in topic_emojis.items() if k in topic_lower), topic_emojis["default"])
        
        markdown_summary += f"""### {emoji} {topic}
{summary}

"""

    markdown_summary += """
---
### 💬 Ask Me Anything!
Feel free to ask me any questions about the document! I'm here to help! ✨
"""

    documents_summary_path = os.path.join(embedding_folder, "documents_summary.txt")
    with open(documents_summary_path, "w", encoding='utf-8') as f:
        f.write(markdown_summary)

    return markdown_summary
