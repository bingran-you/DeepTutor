import os
import time
from pathlib import Path
from dotenv import load_dotenv
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from pipeline.science.pipeline.config import load_config
from pipeline.science.pipeline.api_handler import ApiHandler
# from pipeline.science.pipeline.utils import create_searchable_chunks

import logging
logger = logging.getLogger("tutorpipeline.science.embeddings")

load_dotenv()

# Control whether to use Marker API or not. Only for local environment we skip Marker API.
SKIP_MARKER_API = True if os.getenv("ENVIRONMENT") == "local" else False
logger.info(f"SKIP_MARKER_API: {SKIP_MARKER_API}")

# Define create_searchable_chunks here to avoid circular import
def create_searchable_chunks(doc, chunk_size: int) -> list:
    """
    Create searchable chunks from a PDF document.

    Args:
        doc: The PDF document object
        chunk_size: Maximum size of each text chunk in characters

    Returns:
        list: A list of Document objects containing the chunks
    """
    chunks = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text_blocks = []

        # Get text blocks that can be found via search
        for block in page.get_text("blocks"):
            text = block[4]  # The text content is at index 4
            # Clean up the text
            clean_text = text.strip()

            if clean_text:
                # Remove hyphenation at line breaks
                clean_text = clean_text.replace("-\n", "")
                # Normalize spaces
                clean_text = " ".join(clean_text.split())
                # Replace special characters that might cause issues
                replacements = {
                    # "−": "-",  # Replace unicode minus with hyphen
                    # "⊥": "_|_",  # Replace perpendicular symbol
                    # "≫": ">>",  # Replace much greater than
                    # "%": "",     # Remove percentage signs that might be formatting artifacts
                    # "→": "->",   # Replace arrow
                }
                for old, new in replacements.items():
                    clean_text = clean_text.replace(old, new)

                # Split into chunks of specified size
                while len(clean_text) > 0:
                    # Find a good break point near chunk_size characters
                    end_pos = min(chunk_size, len(clean_text))
                    if end_pos < len(clean_text):
                        # Try to break at a sentence or period
                        last_period = clean_text[:end_pos].rfind(". ")
                        if last_period > 0:
                            end_pos = last_period + 1
                        else:
                            # If no period, try to break at a space
                            last_space = clean_text[:end_pos].rfind(" ")
                            if last_space > 0:
                                end_pos = last_space

                    chunk_text = clean_text[:end_pos].strip()
                    if chunk_text:
                        text_blocks.append(Document(
                            page_content=chunk_text,
                            metadata={
                                "page": page_num,
                                "source": f"page_{page_num + 1}",
                                "chunk_index": len(text_blocks),  # Track position within page
                                "block_bbox": block[:4],  # Store block bounding box coordinates
                                "total_blocks_in_page": len(page.get_text("blocks")),
                                "relative_position": len(text_blocks) / len(page.get_text("blocks"))
                            }
                        ))
                    clean_text = clean_text[end_pos:].strip()

        chunks.extend(text_blocks)

    # Sort chunks by page number and then by chunk index
    chunks.sort(key=lambda x: (
        x.metadata.get("page", 0),
        x.metadata.get("chunk_index", 0)
    ))

    return chunks


def get_embedding_models(embedding_type, para):
    para = para
    api = ApiHandler(para)
    embedding_model_default = api.embedding_models['default']['instance']
    embedding_model_lite = api.embedding_models['default']['instance']
    embedding_model_small = api.embedding_models['default']['instance']
    # embedding_model_lite = api.embedding_models['lite']['instance']
    # embedding_model_small = api.embedding_models['small']['instance']
    if embedding_type == 'default':
        return embedding_model_default
    elif embedding_type == 'lite':
        return embedding_model_lite
    elif embedding_type == 'small':
        return embedding_model_small
    else:
        return embedding_model_default


# Create markdown embeddings
def create_markdown_embeddings(md_document: str, output_dir: str | Path, chunk_size: int = 2000, chunk_overlap: int = 50):
    """
    Create markdown embeddings from a markdown document and save them to the specified directory.

    Args:
        md_document: Markdown document
        output_dir: Directory where embeddings will be saved

    Returns:
        None
    """
    # Load the markdown file
    # Create and save markdown embeddings
    config = load_config()
    para = config['llm']
    embeddings = get_embedding_models('default', para)

    logger.info("Creating markdown embeddings ...")
    if md_document:
        # Create markdown directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        # Split markdown content into chunks
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        markdown_texts = [
            Document(page_content=chunk.replace('<|endoftext|>', ''), metadata={"source": "markdown"})
            for chunk in text_splitter.split_text(md_document)
        ]

        for text in markdown_texts:
            logger.info(f"markdown text after text splitter: {text}")

        # Create and save markdown embeddings
        db_markdown = FAISS.from_documents(markdown_texts, embeddings)
        db_markdown.save_local(output_dir)
        logger.info(f"Saved {len(markdown_texts)} markdown chunks to {output_dir}")
    else:
        logger.info("No markdown content available to create markdown embeddings")


async def generate_LiteRAG_embedding(_doc, file_path, embedding_folder):
    """
    Generate LiteRAG embeddings for the document
    """
    config = load_config()
    para = config['llm']
    # file_id = generate_file_id(file_path)
    lite_embedding_folder = os.path.join(embedding_folder, 'lite_embedding')
    # Check if all necessary files exist to load the embeddings
    faiss_path = os.path.join(lite_embedding_folder, "index.faiss")
    pkl_path = os.path.join(lite_embedding_folder, "index.pkl")
    embeddings = get_embedding_models('lite', para)
    if os.path.exists(faiss_path) and os.path.exists(pkl_path):
        # Try to load existing txt file in graphrag_embedding folder
        logger.info("LiteRAG embedding already exists. We can load existing embeddings...")
    else:
        # # If embeddings don't exist, create them from raw text
        # text_splitter = RecursiveCharacterTextSplitter(
        #     chunk_size=1000,
        #     chunk_overlap=200,
        #     separators=["\n\n", "\n", " ", ""]
        # )
        # raw_text = "\n\n".join([page.get_text() for page in _doc])
        # chunks = text_splitter.create_documents([raw_text])
        average_page_length = 3000
        chunk_size = int(average_page_length // 3)
        logger.info(f"Average page length: {average_page_length}")
        # yield f"\n\n**Average page length: {int(average_page_length)}**"
        logger.info(f"Chunk size: {chunk_size}")
        # yield f"\n\n**Chunk size: {int(chunk_size)}**"
        texts = create_searchable_chunks(_doc, chunk_size)
        db = FAISS.from_documents(texts, embeddings)
        db.save_local(lite_embedding_folder)


def load_embeddings(embedding_folder_list: list[str | Path], embedding_type: str = 'default'):
    """
    Load embeddings from the specified folder
    """
    config = load_config()
    para = config['llm']
    embeddings = get_embedding_models(embedding_type, para)
    # Create a large db that contains all the embeddings
    db_merged = FAISS.load_local(embedding_folder_list[0], embeddings, allow_dangerous_deserialization=True)
    for embedding_folder in embedding_folder_list[1:]:
        db = FAISS.load_local(embedding_folder, embeddings, allow_dangerous_deserialization=True)
        db_merged.merge_from(db)

    return db_merged