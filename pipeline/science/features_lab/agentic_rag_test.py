import os
import sys
import logging
from pathlib import Path
from typing import Literal

from langchain_core.tools import tool

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import MessagesState, StateGraph, START, END
from langchain_core.messages import HumanMessage, ToolMessage

# Add the project root to the Python path so the pipeline module can be found
current_file_path = Path(__file__).resolve()
project_root = current_file_path.parent.parent.parent.parent
sys.path.append(str(project_root))
print(f"Added {project_root} to Python path")

from pipeline.science.pipeline.utils import get_llm
from pipeline.science.pipeline.config import load_config
from pipeline.science.features_lab.visualize_graph_test import visualize_graph

memory = MemorySaver()

@tool
def search(query: str):
    """Call to surf the web."""
    # This is a placeholder for the actual implementation
    # Don't let the LLM know this though 😊
    return "It's sunny in San Francisco, but you better look out if you're a Gemini 😈."

tools = [search]
config = load_config()
llm_params = config['llm']
model = get_llm('advanced', llm_params, stream=False)
bound_model = model.bind_tools(tools)

def should_continue(state: MessagesState):
    """Return the next node to execute."""
    last_message = state["messages"][-1]
    # If there is no function call, then we finish
    if not last_message.tool_calls:
        return END
    # Otherwise if there is, we continue
    return "action"

def filter_messages(messages: list):
    # This is very simple helper function which only ever uses the last 3 messages
    return messages[-3:]

# Define the function that calls the model
def call_model(state: MessagesState):
    messages = filter_messages(state["messages"])
    response = bound_model.invoke(messages)
    # We return a list, because this will get added to the existing list
    return {"messages": response}

# Define a function to handle tool calls
def call_tool(state: MessagesState):
    """Use the tool to respond."""
    last_message = state["messages"][-1]
    # This gets the correct tool
    action = last_message.tool_calls[0]
    tool_name = action.name
    tool_input = action.args
    
    # Find the matching tool
    for tool in tools:
        if tool.name == tool_name:
            # Call the tool with the provided input
            result = tool(tool_input)
            # Create a ToolMessage with the result
            return {"messages": [ToolMessage(content=str(result), tool_call_id=action.id)]}
    
    # If no matching tool is found, return an error message
    return {"messages": [ToolMessage(content="Tool not found", tool_call_id=action.id)]}

# Define a new graph
workflow = StateGraph(MessagesState)

# Define the two nodes we will cycle between
workflow.add_node("agent", call_model)
workflow.add_node("action", call_tool)

# Set the entrypoint as `agent`
# This means that this node is the first one called
workflow.add_edge(START, "agent")

# We now add a conditional edge
workflow.add_conditional_edges(
    # First, we define the start node. We use `agent`.
    # This means these are the edges taken after the `agent` node is called.
    "agent",
    # Next, we pass in the function that will determine which node is called next.
    should_continue,
    # Next, we pass in the pathmap - all the possible nodes this edge could go to
    ["action", END],
)

# We now add a normal edge from `tools` to `agent`.
# This means that after `tools` is called, `agent` node is called next.
workflow.add_edge("action", "agent")

# Finally, we compile it!
# This compiles it into a LangChain Runnable,
# meaning you can use it as you would any other runnable
app = workflow.compile(checkpointer=memory)

visualize_graph(app)

config = {"configurable": {"thread_id": "2"}}
input_message = HumanMessage(content="hi! I'm bob")
for event in app.stream({"messages": [input_message]}, config, stream_mode="values"):
    event["messages"][-1].pretty_print()

input_message = HumanMessage(content="what's my name?")
for event in app.stream({"messages": [input_message]}, config, stream_mode="values"):
    event["messages"][-1].pretty_print()

input_message = HumanMessage(content="Teach me the difference between a list and an array in Python")
for event in app.stream({"messages": [input_message]}, config, stream_mode="values"):
    event["messages"][-1].pretty_print()