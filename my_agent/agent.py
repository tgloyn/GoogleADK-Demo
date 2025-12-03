import os
import asyncio
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm # For Multi-model support
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner 
from google.genai import types # For creating message Content/Parts

from tools import *
from guardrails import *

load_dotenv(override=True)
import warnings
# Ignore all warnings 
warnings.filterwarnings("ignore")

import logging
logging.basicConfig(level=logging.ERROR)

GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY")
OPENAI_API_KEY=os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY")

MODEL_GEMINI_2_0_FLASH = "gemini-2.0-flash"
MODEL_GPT_4O = "openai/gpt-4.1"
MODEL_CLAUDE_SONNET = "anthropic/claude-sonnet-4-20250514"

# --- Define Sub-Agents
greeting_agent = None
try:
    greeting_agent = Agent(
        name='greeting_agent',
        model=LiteLlm(model=MODEL_GPT_4O),
        description="Handles simple greetings and hellos using the 'say_hello' tool.", # Crucial for delegation
        instruction="You are the Greeting Agent. Your ONLY task is to provide a friendly greeting to the user. "
                    "Use the 'say_hello' tool to generate the greeting. "
                    "If the user provides their name, make sure to pass it to the tool. "
                    "Do not engage in any other conversation or tasks.",
        tools=[say_hello]
    )
    print(f"Agent '{greeting_agent.name}' created using model '{greeting_agent.model}'. ")

except Exception as e:
    print(f"❌ Could not create Greeting agent. Check API Key ({greeting_agent.model}). Error: {e}")

farewell_agent = None
try:
    farewell_agent = Agent(
        name='farewell_agent',
        model=LiteLlm(model=MODEL_CLAUDE_SONNET),
        description="Handles simple farewells and goodbyes using the 'say_goodbye' tool.", # Crucial for delegation
        instruction="You are the Farewell Agent. Your ONLY task is to provide a polite goodbye message. "
                    "Use the 'say_goodbye' tool when the user indicates they are leaving or ending the conversation "
                    "(e.g., using words like 'bye', 'goodbye', 'thanks bye', 'see you'). "
                    "Do not perform any other actions.",
        tools=[say_goodbye]
    )
    print(f"Agent '{farewell_agent.name}' created using model '{farewell_agent.model}'. ")

except Exception as e:
    print(f"❌ Could not create farewell agent. Check API Key ({greeting_agent.model}). Error: {e}")

# --- Define Root-Agent
root_agent = None
runner_root = None # Initialize runner

if (greeting_agent and farewell_agent 
    and 'get_weather' 
    and 'block_keyword_guardrail' and 'block_tool_guardrail' in globals()):

    root_agent_model = MODEL_GEMINI_2_0_FLASH

    weather_agent_team = Agent(
        name="weather_agent_v2",
        model=root_agent_model,
        description="Main agent: Handles weather, delegates, includes input AND tool guardrails.",
        instruction="You are the main Weather Agent. Provide weather using 'get_weather_stateful'. "
                    "Delegate greetings to 'greeting_agent' and farewells to 'farewell_agent'. "
                    "Handle only weather, greetings, and farewells.",
        tools=[get_weather],
        sub_agents=[greeting_agent, farewell_agent],
        output_key="last_weather_report", # <<< Auto-save agent's final weather response
        before_model_callback=block_keyword_guardrail, # Assign the guardrail callback 
        before_tool_callback=block_tool_guardrail, # Add tool guardrail
    )
    print(f"✅ Root Agent '{weather_agent_team.name}' created using model '{root_agent_model}' with sub-agents: {[sa.name for sa in weather_agent_team.sub_agents]}")

else:
    print("❌ Cannot create root agent because one or more sub-agents failed to initialize or 'get_weather' tool is missing.")
    if not greeting_agent: print(" - Greeting Agent is missing.")
    if not farewell_agent: print(" - Farewell Agent is missing.")
    if 'get_weather' not in globals(): print(" - get_weather function is missing.")
    if 'block_keyword_guardrail' not in globals(): print("   - 'block_keyword_guardrail' callback")

async def call_agent_async(query: str, runner, user_id, session_id):
    """Sends a query to the agent and prints the final response."""
    print(f"\n>>> User Query: {query}")

    # Prepare the users message in ADK format
    content = types.Content(role='user', parts=[types.Part(text=query)])

    final_response_text = "Agent did not produce a final response." # Default

    # Key concept: run_async executes the agent logic and yields Events.
    # We iterate through events to find the final answer.
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        print(f" [Event] Author: {event.author}, Type: {type(event).__name__}, Final: {event.is_final_response()}, Content: {event.content}")
        
        # Key concept: is_final_response() marks the concluding message for the turn.
        if event.is_final_response():
            if event.content and event.content.parts:
                # Assuming text response in the first part
                final_response_text = event.content.parts[0].text
            elif event.actions and event.actions.escalate: # Handle potential errors/escalations
                final_response_text = f"Agent escalated: {event.error_message or 'No specific message. '}"
            # Add more checks here if needed (e.g. specific error codes)
            break # Stop processing events once the final response is found

        print(f"<<< Agent Response: {final_response_text}")

root_agent_var_name = 'root_agent'
if 'weather_agent_team' in globals():
    root_agent_var_name = 'weather_agent_team'
elif 'root_agent' not in globals():
    print("⚠️ Root agent ('root_agent' or 'weather_agent_team') not found. Cannot define run_team_conversation.")
    # Assign a dummy value to prevent NameError later if the code block runs anyway
    root_agent = None # Or set a flag to prevent execution

# Only define and run if the root agent exists
if root_agent_var_name in globals() and globals()[root_agent_var_name]:
    # define the main async function for the conversation logic.
    async def run_team_conversation():
        print(f"\n--- Testing agent team delegation")
        session_service = InMemorySessionService()
        initial_state = {
            "user_preference_temperature_unit": "Celsius"
        }

        APP_NAME = "weather_tut_ag_team"
        USER_ID = "usr_ag_team_1"
        SESSION_ID = "session_01_ag_team"

        session = await session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID, state=initial_state
        )
        print(f"Session created: App={APP_NAME}, User={USER_ID}, Session={SESSION_ID}")

        # verify initial state was set correctly
        retrieved_session = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
        print("\n--- Initial Session State ---")
        if retrieved_session:
            print(retrieved_session.state)
        else:
            print("Error: could not retrieve session.")

        actual_root_agent = globals()[root_agent_var_name]
        runner_agent_team = Runner(
            agent=actual_root_agent,
            app_name=APP_NAME,
            session_service=session_service
        )
        print(f"Runner created for agent '{actual_root_agent.name}'")

        # --- Interactions using await (correct within async def) ---
        interaction_func = lambda query: call_agent_async(query,
                                                          runner_agent_team,
                                                          USER_ID,
                                                          SESSION_ID)
        
        await interaction_func("What is the weather in New York?")

        await interaction_func("How about Paris?")

        await interaction_func("Tell me the weather in London.")

        print("\n--- Inspecting Final Session State ---")
        final_session = await session_service.get_session(app_name=APP_NAME,
                                                                user_id= USER_ID,
                                                                session_id=SESSION_ID)
        if final_session:
            # Use .get() for safer access to potentially missing keys
            print(f"Final Preference: {final_session.state.get('user_preference_temperature_unit', 'Not Set')}")
            print(f"Final Last Weather Report (from output_key): {final_session.state.get('last_weather_report', 'Not Set')}")
            print(f"Final Last City Checked (by tool): {final_session.state.get('last_city_checked_stateful', 'Not Set')}")
            # Print full state for detailed view
            # print(f"Full State Dict: {final_session.state}") # For detailed view
        else:
            print("\n❌ Error: Could not retrieve final session state.")


if __name__ == "__main__":
    try:
        asyncio.run(run_team_conversation())
    except Exception as e:
        print(f"An error occurred: {e}")


