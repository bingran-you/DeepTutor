import streamlit as st
import asyncio
import json
import logging
import queue
import threading
from pipeline.science.pipeline.tutor_agent import tutor_agent, extract_lite_mode_content, extract_basic_mode_content, extract_advanced_mode_content
from pipeline.science.pipeline.get_response import generate_follow_up_questions
from pipeline.science.pipeline.session_manager import ChatSession, ChatMode
from typing import Generator, Any, AsyncGenerator

logger = logging.getLogger("tutorfrontend.utils")


async def streamlit_tutor_agent(chat_session, file_path, user_input):    
    answer_generator = await tutor_agent(
        chat_session=chat_session,
        file_path_list=[file_path],
        user_input=user_input,
        deep_thinking=True
    )
    answer = ""
    sources = {}
    source_pages = {}
    source_annotations = {}
    refined_source_pages = {}
    refined_source_index = {}
    follow_up_questions = []
    thinking = ""
    # Check the session type and set the extract_content_func
    if chat_session.mode == ChatMode.LITE:
        extract_content_func = extract_lite_mode_content
    elif chat_session.mode == ChatMode.BASIC:
        extract_content_func = extract_basic_mode_content
    elif chat_session.mode == ChatMode.ADVANCED:
        extract_content_func = extract_advanced_mode_content
    answer, sources, source_pages, source_annotations, refined_source_pages, refined_source_index, follow_up_questions, thinking = extract_content_func(chat_session.current_message)
    return answer_generator, sources, source_pages, source_annotations, refined_source_pages, refined_source_index, follow_up_questions


def format_reasoning_response(thinking_content):
    """Format assistant content by removing think tags."""
    return (
        thinking_content.replace("<think>\n\n</think>", "")
        .replace("<think>", "")
        .replace("</think>", "")
    )


def format_response(response_content):
    """Format assistant content by removing think tags."""
    return (
        response_content.replace("<response>\n\n</response>", "")
        .replace("<response>", "")
        .replace("</response>", "")
    )


def sync_generator_from_async(async_gen):
    """Convert an async generator to a synchronous generator using threading"""
    queue = queue.Queue()
    end_marker = object()  # Unique object to signal the end of the generator
    
    def fill_queue():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def consume():
            try:
                async for item in async_gen:
                    queue.put(item)
            except Exception as e:
                queue.put(e)
            finally:
                queue.put(end_marker)
                
        loop.run_until_complete(consume())
        loop.close()
    
    thread = threading.Thread(target=fill_queue)
    thread.daemon = True
    thread.start()
    
    while True:
        item = queue.get()
        if item is end_marker:
            break
        if isinstance(item, Exception):
            raise item
        yield item


def run_async_in_thread(async_func, *args, **kwargs):
    """Run an async function in a separate thread with its own event loop"""
    result_queue = queue.Queue()
    
    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(async_func(*args, **kwargs))
            result_queue.put(("result", result))
        except Exception as e:
            result_queue.put(("error", e))
        finally:
            loop.close()
    
    thread = threading.Thread(target=worker)
    thread.daemon = True  # Allow the program to exit even if the thread is running
    thread.start()
    
    result_type, result = result_queue.get()
    if result_type == "error":
        raise result
    return result


def process_response_phase(response_placeholder, stream_response: Generator, mode: ChatMode = None, stream: bool = False):
    """
    Process the response phase of the assistant's response.
    Args:
        stream_response: The generator object from the stream response.
    Returns:
        The response content as a string.
    """
    if stream:
        # If stream_response is an async generator, convert it to a sync generator
        if hasattr(stream_response, "__aiter__"):
            stream_response = sync_generator_from_async(stream_response)
            
        response_content = response_placeholder.write_stream(stream_response)
        # response_content = ""
        # with st.status("Responding...", expanded=True) as status:
        #     response_placeholder = st.empty()
            
        #     for chunk in stream_response:
        #         content = chunk or ""
        #         response_content += content
                
        #         if "<response>" in content:
        #             continue
        #         if "</response>" in content:
        #             content = content.replace("</response>", "")
        #             status.update(label="Responding complete!", state="complete", expanded=True)
        #             return response_content
        #         response_placeholder.markdown(format_response(response_content))

        # return response_content

    else:
        response_placeholder.write(stream_response)
        response_content = stream_response
    logger.info(f"Final whole response content: {response_content}")
    return response_content


def process_thinking_phase(response_placeholder, answer):
    """Process the thinking phase of the assistant's response."""
    thinking_content = ""
    with st.status("Thinking...", expanded=True) as status:
        think_placeholder = st.empty()
        
        for chunk in answer:
            content = chunk or ""
            thinking_content += content
            
            if "<think>" in content:
                continue
            if "</think>" in content:
                content = content.replace("</think>", "")
                status.update(label="Thinking complete!", state="complete", expanded=False)
                return thinking_content
            think_placeholder.markdown(format_reasoning_response(thinking_content))
    
    # return format_reasoning_response(thinking_content)
    return thinking_content


# Function to display the pdf
def previous_page():
    logger.info("previous_page")
    if st.session_state.current_page > 1:
        st.session_state.current_page = st.session_state.current_page - 1
    logger.info(f"st.session_state.current_page is {st.session_state.current_page}")


# Function to display the pdf
def next_page():
    logger.info("next_page")
    if st.session_state.current_page < st.session_state.total_pages:
        st.session_state.current_page = st.session_state.current_page + 1
    logger.info(f"st.session_state.current_page is {st.session_state.current_page}")


# Function to close the pdf
def close_pdf():
    st.session_state.show_pdf = False


# Function to open the pdf
def file_changed():
    for key in list(st.session_state.keys()):
        del st.session_state[key]


# Function to handle follow-up question clicks
def handle_follow_up_click(chat_session: ChatSession, question: str):
    """Handle when a user clicks on a follow-up question.
    
    Args:
        question: The follow-up question text that was clicked
    """
    st.session_state.next_question = question
    
    # Create a temporary chat history for context-specific follow-up questions
    temp_chat_history = []
    
    # Find the last assistant message that generated this follow-up question
    for i in range(len(st.session_state.chat_session.chat_history) - 1, -1, -1):
        msg = st.session_state.chat_session.chat_history[i]
        if msg["role"] == "assistant" and "follow_up_questions" in msg:
            if question in msg["follow_up_questions"]:
                # Include the context: previous user question and assistant's response
                if i > 0 and st.session_state.chat_session.chat_history[i-1]["role"] == "user":
                    temp_chat_history.append(st.session_state.chat_session.chat_history[i-1])  # Previous user question
                temp_chat_history.append(msg)  # Assistant's response
                break
    
    # Store the temporary chat history in session state for the agent to use
    st.session_state.temp_chat_history = temp_chat_history
    
    # Add the new question to the full chat history
    st.session_state.chat_session.chat_history.append(
        {"role": "user", "content": question}
    )
    # Update chat session history
    st.session_state.chat_session.chat_history = st.session_state.chat_session.chat_history
