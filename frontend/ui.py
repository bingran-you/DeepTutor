import json
import base64
import PyPDF2
import streamlit as st
from typing import Set
from streamlit_pdf_viewer import pdf_viewer
from streamlit_float import float_init, float_parent, float_css_helper
from streamlit_extras.stylable_container import stylable_container

from frontend.utils import (
    previous_page,
    next_page,
    handle_follow_up_click,
    format_reasoning_response
)
from frontend.forms.contact import contact_form
from pipeline.science.pipeline.config import load_config
from pipeline.science.pipeline.session_manager import ChatMode
from frontend.utils import streamlit_tutor_agent, process_response_phase, process_thinking_phase

import logging
import re
import os

logger = logging.getLogger("tutorfrontend.ui")

def to_emoji_number(num: int) -> str:
    """Convert an integer to a bold circled number (1-20).
    
    Args:
        num: Integer to convert
        
    Returns:
        String containing the bold circled number representation for 1-20,
        or regular number for values > 20
    """
    # Use circled numbers for 1-50
    circled_numbers = [
        "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
        "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
        "㉑", "㉒", "㉓", "㉔", "㉕", "㉖", "㉗", "㉘", "㉙", "㉚",
        "㉛", "㉜", "㉝", "㉞", "㉟", "㊱", "㊲", "㊳", "㊴", "㊵",
        "㊶", "㊷", "㊸", "㊹", "㊺", "㊻", "㊼", "㊽", "㊾", "㊿"
    ]
    if 1 <= num <= len(circled_numbers):
        return circled_numbers[num - 1]
    return str(num)  # Use regular number if > 20


# Function to set up the page configuration
def setup_page_config():
    st.set_page_config(
        page_title="DeepTutor",
        # page_icon="frontend/images/professor.svg",
        page_icon="frontend/images/logo.png",
        layout="wide",
        # initial_sidebar_state="collapsed",
        initial_sidebar_state="expanded"
    )


def show_auth_top():
    # st.write("")
    pass


# Function to display the header
def show_header():
    with st.sidebar:
        with open("frontend/images/logo.png", "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode()
        st.markdown(
            f"""
            <h1 style='text-align: left;'>
                <img src="data:image/png;base64,{encoded_image}" alt='icon' style='width:50px; height:50px; vertical-align: left; margin-right: 10px;'>
                DeepTutor
            </h1>
            """,
            unsafe_allow_html=True
        )
        st.subheader(" ")
        # st.subheader("Upload a document to get started.")


# Function to display the response mode options
def show_mode_option():
    with st.sidebar:
        mode_index = 1
        current_mode = st.radio(
            "Choose a mode:",
            options=["Lite", "Basic", "Advanced"],
            help="""
            - Lite: Process raw text only (fastest)
            - Basic: Agentic processing with Markdown extraction, image understanding, DeepSeek R1 deep thinking, and document summarization (standard)
            - Advanced: In addition to Basic, add enhanced GraphRAG for better document understanding (slower but more accurate)
            """,
            index=mode_index
        )
        st.session_state.mode = current_mode
        if 'chat_session' in st.session_state:
            if current_mode == "Advanced":
                st.session_state.chat_session.set_mode(ChatMode.ADVANCED)
            elif current_mode == "Basic":
                st.session_state.chat_session.set_mode(ChatMode.BASIC)
            else:
                st.session_state.chat_session.set_mode(ChatMode.LITE)


# Function to display the file uploader
def show_file_upload(on_change=None):
    with st.sidebar:
        previous_file = st.session_state.get('uploaded_file', None)
        
        # Get current mode to decide if multiple uploads are allowed
        current_mode = st.session_state.get('mode', 'Basic')
        allow_multiple = current_mode == "Lite"
        
        # Use the appropriate file uploader based on mode
        if allow_multiple:
            current_file = st.file_uploader("Upload documents no more than **200 pages** to get started.", 
                                          type="pdf", 
                                          on_change=on_change,
                                          accept_multiple_files=True)
        else:
            current_file = st.file_uploader("Upload a document no more than **200 pages** to get started.", 
                                          type="pdf", 
                                          on_change=on_change)
        
        # Check if file has changed
        if previous_file is not None and current_file is not None:
            if allow_multiple:
                # For multiple files, check if the list has changed
                if previous_file != current_file:
                    on_change()
            else:
                # For single file, check if the file name has changed
                if previous_file.name != current_file.name:
                    on_change()
        
        st.session_state.uploaded_file = current_file
        
        # Set upload state
        if st.session_state.get('is_uploaded_file', None):
            st.session_state['is_uploaded_file'] = True


# Function to display the language selection options in the sidebar
def show_language_option():
    """Function to display the language selection options in the sidebar."""
    with st.sidebar:
        # Load languages from config
        config = load_config()
        languages = config['languages']

        # Get current language from session state or default to English
        current_lang = st.session_state.get("language", "English")
        
        # Create the language selector
        selected_lang_display = st.selectbox(
            "🌐 Language",
            options=list(languages.keys()),
            index=list(languages.values()).index(current_lang)
        )
        
        # Update both session state and chat session with the selected language
        selected_lang = languages[selected_lang_display]
        st.session_state.language = selected_lang
        if 'chat_session' in st.session_state:
            st.session_state.chat_session.set_language(selected_lang)


# Function to display the page option
def show_page_option():
    with st.sidebar:
        # Navigation Menu
        menu = ["📑 Document reading", "📬 DeepTutor?"]
        st.session_state.page = st.selectbox("🖥️ Page", menu)


def get_relevance_color(score):
    """Convert a relevance score to a shade of grey.
    
    Args:
        score: Float between 0 and 1
        
    Returns:
        Hex color code string for a shade of grey, where:
        - High relevance (1.0) = Dark grey (#404040)
        - Medium relevance (0.5) = Medium grey (#808080)
        - Low relevance (0.0) = Light grey (#C0C0C0)
    """
    # Convert score to a grey value between 192 (C0) and 100 (40)
    grey_value = int(192 - (score * 92))
    return f"#{grey_value:02x}{grey_value:02x}{grey_value:02x}"


# Function to display the chat interface
def show_chat_interface(doc, document, file_path, embedding_folder):
    # Handle file_path as a list for multiple files or a single string
    file_path_for_agent = file_path if isinstance(file_path, list) else [file_path]
    
    # Init float function for chat_input textbox
    learner_avatar = "frontend/images/learner.svg"
    tutor_avatar = "frontend/images/tutor.svg"
    professor_avatar = "frontend/images/professor.svg"
    config = load_config()
    stream = config["stream"]
    with st.container():
        float_init(theme=True, include_unstable_primary=False)
        user_input = st.chat_input(key='user_input')
        if user_input:
            st.session_state.chat_session.chat_history.append({"role": "user", "content": user_input})
        button_b_pos = "1.2rem"
        button_css = float_css_helper(width="1.2rem", bottom=button_b_pos, transition=0)
        float_parent(css=button_css)

    chat_container = st.container(border=st.session_state.show_chat_border, height=1005)

    with chat_container:
        # Display list of uploaded files if in Lite mode with multiple files
        if isinstance(file_path, list) and len(file_path) > 1 and st.session_state.get('mode') == "Lite":
            with st.expander("📚 Uploaded Files", expanded=False):
                for i, path in enumerate(file_path, 1):
                    filename = os.path.basename(path)
                    st.write(f"{i}. {filename}")
            st.markdown("---")
        
        if st.session_state.chat_session.chat_history:
            # Display chat history
            logger.info("Display chat history ...")
            for idx, msg in enumerate(st.session_state.chat_session.chat_history):
                # # TEST
                # logger.info(f"chat history: {st.session_state.chat_session.chat_history}")
                if msg["role"] == "user":
                    avatar = learner_avatar
                    with st.chat_message(msg["role"], avatar=avatar):
                        st.write(msg["content"])
                elif msg["role"] == "assistant":
                    avatar = professor_avatar if st.session_state.chat_session.mode == ChatMode.ADVANCED else tutor_avatar
                    with st.chat_message(msg["role"], avatar=avatar):
                        # For chat history, we need to recreate the expandable UI for thinking content
                        content = msg["content"]

                        # Handle thinking tags first (before displaying main content)
                        pattern = r"<thinking>(.*?)</thinking>"
                        think_match = re.search(pattern, content, re.DOTALL)
                        if think_match:
                            think_content = think_match.group(0)
                            content = content.replace(think_content, "")
                            think_content = format_reasoning_response(think_content)
                            with st.expander("Thinking complete!"):
                                st.markdown(think_content)

                        # Extract appendix content to display later
                        appendix_content = None
                        pattern = r"<appendix>(.*?)</appendix>"
                        appendix_match = re.search(pattern, content, re.DOTALL)
                        if appendix_match:
                            appendix_content = appendix_match.group(0)
                            content = content.replace(appendix_content, "")
                            appendix_content = appendix_content.replace("<appendix>", "").replace("</appendix>", "")

                        # Display the main content
                        st.markdown(content)
                        
                        # Display appendix after the main content
                        if appendix_content:
                            with st.expander("Additional information"):
                                st.markdown(appendix_content)
                        
                        # First display source buttons if this message has associated sources
                        next_msg = st.session_state.chat_session.chat_history[idx + 1] if idx + 1 < len(st.session_state.chat_session.chat_history) else None
                        if next_msg and next_msg["role"] == "source_buttons":
                            sources = next_msg["sources"]
                            # Convert sources to dict if it's a list (for backward compatibility)
                            if isinstance(sources, list):
                                sources = {source: 1.0 for source in sources}  # Assign max relevance to old sources
                                next_msg["sources"] = sources
                            
                            if sources and len(sources) > 0:
                                st.write("\n\n**📚 Sources:**")
                                # Sort sources by page numbers
                                sorted_sources = sorted(sources.items(), key=lambda x: next_msg["pages"][x[0]])
                                cols = st.columns(len(sources))
                                for src_idx, (col, (source, score)) in enumerate(zip(cols, sorted_sources), 1):
                                    page_num = next_msg["pages"][source]
                                    with col:
                                        # Create a stylable container for the button with custom color
                                        button_color = get_relevance_color(score)
                                        button_style = """
                                        button {{
                                            background-color: {button_color} !important;
                                            border-color: {button_color} !important;
                                            color: white !important;
                                            transition: filter 0.2s !important;
                                        }}
                                        button:hover {{
                                            background-color: {button_color} !important;
                                            border-color: {button_color} !important;
                                            filter: brightness(120%) !important;
                                        }}
                                        """
                                        with stylable_container(
                                            key=f"source_btn_container_{idx}_{src_idx}",
                                            css_styles=button_style.format(button_color=button_color)
                                        ):
                                            if st.button(to_emoji_number(src_idx), key=f"source_btn_{idx}_{src_idx}", use_container_width=True):
                                                st.session_state.current_page = page_num
                                                # Display the highlight info for that single source button
                                                # st.session_state.annotations, st.session_state.react_annotations = get_highlight_info(doc, [source])
                                                try:
                                                    image_extensions: Set[str] = set(config["image_extensions"])
                                                    is_image_file = any(source.lower().endswith(ext.lower()) for ext in image_extensions)
                                                    
                                                    if is_image_file:
                                                        # For image files, use empty annotations
                                                        st.session_state.annotations = []
                                                    else:
                                                        # For other files, get annotations from source_annotations
                                                        st.session_state.annotations = st.session_state.source_annotations[source]
                                                except Exception as e:
                                                    logger.exception(f"Failed to get annotations: {str(e)}")
                                                    st.session_state.annotations = []
                        
                        # Then display follow-up questions
                        if "follow_up_questions" in msg and msg["follow_up_questions"] != []:
                            # st.write("\n\n**📝 Follow-up Questions:**")
                            for q_idx, question in enumerate(msg["follow_up_questions"], 1):
                                if st.button(f"{q_idx}. {question}", key=f"follow_up_{idx}_{q_idx}"):
                                    handle_follow_up_click(st.session_state.chat_session, question)
                elif msg["role"] == "source_buttons":
                    # Skip source buttons here since we're showing them with the assistant message
                    pass
        else:
            user_input = config["summary_wording"]

        if user_input != config["summary_wording"]:
            # If there's a next question from follow-up click, process it
            if "next_question" in st.session_state:
                user_input = st.session_state.next_question
                del st.session_state.next_question
            else:
                user_input = st.session_state.get('user_input', None)

        # If we have input to process
        if user_input:
            logger.info(f"Processing user_input: {user_input}...")
            with st.spinner("Generating deep agentic response..."):
                try:
                    # Get response
                    # answer, sources, source_pages, source_annotations, refined_source_pages, follow_up_questions, refined_source_index
                    answer,\
                    sources,\
                    source_pages,\
                    source_annotations,\
                    refined_source_pages,\
                    refined_source_index,\
                    follow_up_questions = streamlit_tutor_agent(
                        chat_session=st.session_state.chat_session,
                        file_path_list=file_path_for_agent,
                        user_input=user_input
                    )
                    st.session_state.source_annotations = source_annotations

                    # Convert sources to dict if it's a list (for backward compatibility)
                    if isinstance(sources, list):
                        sources = {source: 1.0 for source in sources}  # Assign max relevance to old sources
                    else:
                        # Validate sources is a dictionary
                        sources = sources if isinstance(sources, dict) else {}
                    
                    # Display current response
                    response_placeholder = st.chat_message("assistant", avatar=tutor_avatar)
                    with response_placeholder:
                        # For live responses, process_thinking_phase already displays the thinking UI
                        # We just need to capture the content and format it for storage
                        # answer_content = process_thinking_phase(response_placeholder, answer)
                        # thinking_content = process_thinking_phase(response_placeholder, answer)
                        response_content = process_response_phase(response_placeholder, stream_response=answer, mode=st.session_state.chat_session.mode, stream = stream)
                        # def process_response_phase(response_placeholder, stream_response: Generator, mode: ChatMode = None, stream: bool = False):
                        answer_content = response_content
                        
                        # # Display the content directly as markdown
                        # # The thinking UI was already shown by process_thinking_phase
                        # st.markdown(answer_content)
                        
                        # First display source buttons
                        if sources and len(sources) > 0:
                            st.write("\n\n**📚 Sources:**")

                            # Sort sources by page numbers
                            sorted_sources = sorted(sources.items(), key=lambda x: refined_source_pages.get(x[0], 0))
                            cols = st.columns(len(sources))
                            for idx, (col, (source, score)) in enumerate(zip(cols, sorted_sources), 1):
                                page_num = refined_source_pages.get(source)
                                if page_num:
                                    with col:
                                        # Create a stylable container for the button with custom color
                                        button_color = get_relevance_color(score)
                                        button_style = """
                                        button {{
                                            background-color: {button_color} !important;
                                            border-color: {button_color} !important;
                                            color: white !important;
                                            transition: filter 0.2s !important;
                                        }}
                                        button:hover {{
                                            background-color: {button_color} !important;
                                            border-color: {button_color} !important;
                                            filter: brightness(120%) !important;
                                        }}
                                        """
                                        with stylable_container(
                                            key=f"source_btn_container_current_{idx}",
                                            css_styles=button_style.format(button_color=button_color)
                                        ):
                                            if st.button(to_emoji_number(idx), key=f"source_btn_{idx}_current", use_container_width=True):
                                                st.session_state.current_page = page_num
                                                # st.session_state.annotations, st.session_state.react_annotations = get_highlight_info(doc, [source])
                                                # i = list(sources.keys()).index(source)
                                                try:
                                                    image_extensions: Set[str] = set(config["image_extensions"])
                                                    is_image_file = any(source.lower().endswith(ext.lower()) for ext in image_extensions)
                                                    
                                                    if is_image_file:
                                                        # For image files, use empty annotations
                                                        st.session_state.annotations = []
                                                    else:
                                                        # For other files, get annotations from source_annotations
                                                        st.session_state.annotations = st.session_state.source_annotations[source]
                                                except Exception as e:
                                                    logger.exception(f"Failed to get annotations: {str(e)}")
                                                    st.session_state.annotations = []
                                                # logger.info(f"type of st.session_state.annotations: {type(st.session_state.annotations)}")

                                            # Add response with follow-up questions to chat history
                        
                        # Then display follow-up questions
                        # st.write("\n\n**📝 Follow-up Questions:**")
                        for q_idx, question in enumerate(follow_up_questions, 1):
                            if st.button(f"{q_idx}. {question}", key=f"follow_up_current_{q_idx}"):
                                handle_follow_up_click(st.session_state.chat_session, question)

                    st.session_state.sources = sources
                    st.session_state.chat_occurred = True

                    st.session_state.chat_session.chat_history.append(
                        {
                            "role": "assistant", 
                            "content": answer_content,
                            "follow_up_questions": follow_up_questions
                        }
                    )

                    # Add source buttons to chat history
                    st.session_state.chat_session.chat_history.append({
                        "role": "source_buttons",
                        "sources": sources,
                        "pages": refined_source_pages,
                        "annotations": st.session_state.source_annotations,
                        "timestamp": len(st.session_state.chat_session.chat_history)
                    })

                except json.JSONDecodeError:
                    st.error("There was an error parsing the response. Please try again.")


            # Highlight PDF excerpts
            if doc and st.session_state.get("chat_occurred", False):
                if "current_page" not in st.session_state:
                    st.session_state.current_page = 1
                if st.session_state.get("sources"):
                    # st.session_state.annotations, st.session_state.react_annotations = get_highlight_info(doc, list(st.session_state.sources.keys()))
                    # i = list(st.session_state.sources.keys()).index(source)
                    image_extensions: Set[str] = set(config["image_extensions"])
                    try:
                        # Check if source is an image file by checking its extension
                        is_image_file = any(source.lower().endswith(ext.lower()) for ext in image_extensions)
                        
                        if is_image_file:
                            # For image files, use empty annotations
                            st.session_state.annotations = []
                        else:
                            # For other files, get annotations from source_annotations
                            st.session_state.annotations = st.session_state.source_annotations[source]
                    except Exception as e:
                        logger.exception(f"Failed to get annotations: {str(e)}")
                        st.session_state.annotations = []
                    # logger.info(f"type of st.session_state.annotations: {type(st.session_state.annotations)}")


# Function to display the pdf viewer
def show_pdf_viewer(file):
    """
    Display the PDF viewer.
    
    Args:
        file: Either a file object or a file path to the PDF
    """
    if "current_page" not in st.session_state:
        logger.info("current_page not in st.session_state")
        st.session_state.current_page = 1
    if "annotations" not in st.session_state:
        logger.info("annotations not in st.session_state")
        st.session_state.annotations = []
    if "total_pages" not in st.session_state:
        logger.info("total_pages not in st.session_state")
        # Get total pages from the PDF file
        if isinstance(file, str):
            # If file is a path, open it
            pdf = PyPDF2.PdfReader(open(file, 'rb'))
        else:
            # If file is a file object
            pdf = PyPDF2.PdfReader(file)
        st.session_state.total_pages = len(pdf.pages)

    with st.container():
        st.markdown("""
        <style>
        .fullHeight {
            height: 80vh;
            width: 100%;
            max-width: 100%;
            overflow: auto;
        }
        .stPdfViewer {
            width: 100% !important;
            height: auto !important;
        }
        </style>
        """, unsafe_allow_html=True)
    
    # Create a unique key for the PDF container based on current page
    pdf_container = st.container(border=st.session_state.show_chat_border, height=1005, key=f"pdf_container_{st.session_state.current_page}")

    with pdf_container:
        # Use current_page in the key to force refresh when page changes
        pdf_viewer(
            file,
            width="100%",
            annotations=st.session_state.annotations,
            pages_to_render=[st.session_state.current_page],
            render_text=True,
            key=f"pdf_viewer_{st.session_state.current_page}_{st.session_state.annotations}"
        )

    # Create three columns for the navigation controls
    columns = st.columns([1, 2, 1])
    
    # Left arrow button
    with columns[0]:
        with stylable_container(
            key=f"left_aligned_button_{st.session_state.current_page}",
            css_styles="""
            button {
                float: left;
            }
            """
        ):
            st.button("←", key=f"prev_{st.session_state.current_page}", on_click=previous_page)
            button_css = float_css_helper(width="1.2rem", bottom="1.2rem", transition=0)
            float_parent(css=button_css)
    
    # Page counter in the middle
    with columns[1]:
        st.markdown(
            f"""<div style="text-align: center; color: #666666;">
                Page {st.session_state.current_page} of {st.session_state.total_pages}
                </div>""",
            unsafe_allow_html=True
        )
        
    # Right arrow button
    with columns[2]:
        with stylable_container(
            key=f"right_aligned_button_{st.session_state.current_page}",
            css_styles="""
            button {
                float: right;
            }
            """
        ):
            st.button("→", key=f"next_{st.session_state.current_page}", on_click=next_page)
            button_css = float_css_helper(width="1.2rem", bottom="1.2rem", transition=0)
            float_parent(css=button_css)


# Function to display the footer
def show_footer():
    with st.sidebar:
        st.markdown("---")
        st.markdown("**DeepTutors** can make mistakes, sometimes you have to trust **YOURSELF**! 🧠")


@st.dialog("Contact Us")
def show_contact_form():
    contact_form()


# Function to display the contact us page
def show_contact_us():
    st.title("📬 Contact Us")
    st.markdown("""
    We'd love to hear from you! Whether you have any **question, feedback, or want to contribute**, feel free to reach out.

    - **Email:** [knowhiz.us@gmail.com](mailto:knowhiz.us@gmail.com) 📨
    - **Discord:** [Join our Discord community](https://discord.gg/7ucnweCKk8) 💬
    - **GitHub:** [Contribute on GitHub](https://github.com/DeepTutor/DeepTutor) 🛠️
    - **Follow us:** [LinkedIn](https://www.linkedin.com/company/knowhiz) | [Twitter](https://x.com/knowhizlearning) 🏄

    If you'd like to request a feature or report a bug, please **let us know!** Your suggestions are highly appreciated! 🙌
    """)
    if st.button("Feedback Form"):
        show_contact_form()
    st.title("🗂️ KnoWhiz flashcards")
    st.markdown("Want more **structured and systematic** learning? Check out our **[KnoWhiz flashcards learning platform](https://www.knowhiz.us/)!** 🚀")