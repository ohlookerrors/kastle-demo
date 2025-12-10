"""
Outbound Call Handler - Streamlined version with Node Engine integration.

This file handles:
- Twilio WebSocket connections
- Deepgram Voice Agent management
- Audio routing (Twilio <-> Deepgram)
- Recording management
- Node Engine integration for conversation flow

All conversation logic is now handled by NodeEngine via nodes.json.
"""

from fastapi import WebSocket, APIRouter, WebSocketDisconnect, HTTPException, status, Request, Form
import websockets
import os
import asyncio
from typing import Optional, Dict, Any
import json
from datetime import datetime, timezone
import base64
from dotenv import load_dotenv
import random
from urllib.parse import quote
import logging

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream, Dial, Say
from fastapi.responses import Response

# Import config first to initialize logging
from app.config import logger

# Node Engine imports
from app.utils.node_engine import get_node_engine, NodeEngine
from app.utils.context_manager import context_manager

# Keep these utility imports
from app.utils.prompt_gen import get_outbound_prompt_multilingual
from app.utils.agents import get_agents
from app.utils.teams import get_team
from app.services.get_outbound_data import fetch_caller_data, fetch_client_data
from app.utils.memo_builder import MemoBuilder
from app.services.memo_api_service import post_memo
from app.services.collection_dates_api import post_collection_activity

load_dotenv()

outbound_router = APIRouter()

# ========== ENVIRONMENT VARIABLES ==========
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER_OUTBOUND = os.getenv("TWILIO_PHONE_NUMBER_OUTBOUND")
SERVER_URL = os.getenv("SERVER_URL")
CALLBACK_PHONE_NUMBER = os.getenv("CALLBACK_PHONE_NUMBER", "+918956580955")
TRANSFER_PHONE_NUMBER = os.getenv("TRANSFER_PHONE_NUMBER", "+918956580955")

# ========== CLIENTS ==========
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
api_key = os.getenv("DEEPGRAM_API_KEY")

# Initialize Node Engine
node_engine = get_node_engine()

# ========== GLOBAL STATE ==========
active_calls: Dict[str, dict] = {}
recording_mappings: Dict[str, str] = {}

# ========== SCREENING DETECTION PHRASES ==========
SCREENING_PHRASES = [
    "record your name", "leave a message", "after the tone", "after the beep",
    "please leave", "reason for calling", "record a message", "press 1 to accept",
    "screening service", "unknown caller"
]


# =============================================================================
# TWILIO ENDPOINTS
# =============================================================================

@outbound_router.post("/makecall")
async def make_call(to_number: str, custom_greeting: str = "", customer_data: Optional[Dict] = None):
    """Initiate outbound call with AMD enabled"""
    try:
        if not to_number.startswith("+"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid phone number format. Must start with '+' and country code."
            )
        
        logger.info(f"Initiating outbound call to {to_number}")

        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER_OUTBOUND,
            url=f"{SERVER_URL}/outbound/twilml?caller_phone={quote(TWILIO_PHONE_NUMBER_OUTBOUND)}&phone={quote(to_number)}",
            
            # AMD Configuration
            machine_detection="DetectMessageEnd",
            machine_detection_timeout=30,
            machine_detection_speech_threshold=2400,
            machine_detection_speech_end_threshold=1200,
            machine_detection_silence_timeout=5000,
            
            async_amd="true",
            async_amd_status_callback=f"{SERVER_URL}/outbound/amd_callback",
            async_amd_status_callback_method="POST",
            
            status_callback=f"{SERVER_URL}/outbound/call_status",  
            status_callback_event=["initiated", "ringing", "answered", "completed"],

            # Recording
            # record=True,
            # recording_channels="dual",
            # recording_status_callback=f"{SERVER_URL}/outbound/recording_status",
            # recording_status_callback_event=["completed", "absent"],
        )

        logger.info(f"Call initiated with SID: {call.sid}")

        return {
            "status": "success",
            "call_sid": call.sid,
            "to": to_number,
            "from": TWILIO_PHONE_NUMBER_OUTBOUND,
            "message": "Outbound call initiated with Node Engine"
        }
    except Exception as e:
        logger.error(f"Error making call: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# @outbound_router.post("/recording_status")
# async def recording_status_webhook(request: Request):
#     """Handle recording status updates from Twilio"""
#     form_data = await request.form()
#     call_sid = form_data.get("CallSid")
#     recording_sid = form_data.get("RecordingSid")
#     recording_url = form_data.get("RecordingUrl")
#     status = form_data.get("RecordingStatus")
    
#     logger.info(f"Recording status: {status} for call {call_sid}, recording {recording_sid}")
    
#     if recording_sid and call_sid:
#         recording_mappings[call_sid] = recording_sid
    
#     return {"status": "received"}


@outbound_router.post("/amd_callback")
async def amd_callback(request: Request):
    """Handle AMD (Answering Machine Detection) results"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    answered_by = form_data.get("AnsweredBy")
    
    logger.info(f"AMD Result for {call_sid}: {answered_by}")
    
    if answered_by in ["machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other"]:
        logger.info(f"Voicemail detected for {call_sid}")
        # Handle voicemail - could redirect or hang up
    
    return {"status": "received"}


@outbound_router.post("/call_status")
async def call_status_webhook(request: Request):
    """Handle call status updates from Twilio"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    
    logger.info(f"Call status update: {call_sid} -> {call_status}")
    
    return {"status": "received"}


@outbound_router.api_route("/twilml", methods=["GET", "POST"])
async def generate_twiml(caller_phone: str, phone: str):
    """Generate TwiML for connecting to WebSocket"""
    response = VoiceResponse()
    
    connect = Connect()
    stream = Stream(
        url=f"wss://{SERVER_URL.replace('https://', '')}/outbound/twilio/{quote(caller_phone)}/{quote(phone)}"
    )
    connect.append(stream)
    response.append(connect)
    
    return Response(content=str(response), media_type="application/xml")


@outbound_router.post("/transfer")
async def transfer_call(call_sid: str = Form(...), phone: str = Form(...)):
    """Handle call transfer to Level 2"""
    try:
        logger.info(f"Transferring call {call_sid} to {phone}")
        
        twilio_client.calls(call_sid).update(
            url=f"{SERVER_URL}/outbound/transfer_twiml?phone={quote(phone)}",
            method="POST"
        )
        
        return {"status": "transferring", "to": phone}
    except Exception as e:
        logger.error(f"Transfer failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@outbound_router.get("/transfer_twiml")
async def transfer_twiml(phone: str):
    """Generate TwiML for transfer"""
    response = VoiceResponse()
    response.say("Please hold while I transfer you to a specialist.")
    response.dial(phone)
    return Response(content=str(response), media_type="application/xml")


# =============================================================================
# DEEPGRAM CONNECTION
# =============================================================================

async def connect_to_deepgram():
    """Connect to Deepgram Voice Agent WebSocket"""
    deepgram_url = "wss://agent.deepgram.com/v1/agent/converse"
    
    logger.info("Connecting to Deepgram Voice Agent...")
    try:
        connection = await websockets.connect(
            deepgram_url,
            subprotocols=["token", api_key],
            ping_interval=20,
            ping_timeout=10
        )
        logger.info("Connected to Deepgram Voice Agent!")
        return connection
    except Exception as e:
        logger.error(f"Failed to connect to Deepgram: {e}")
        raise


def get_agent_config(
    customer: Dict[str, Any],
    selected_agent: Dict[str, Any],
    language: str = "en",
    master_prompt: str = None,
    greeting_prompt: str = None
) -> dict:
    """
    Get Deepgram Voice Agent configuration.

    Args:
        customer: Customer data dictionary
        selected_agent: Agent configuration
        language: Language code ('en' or 'es')
        master_prompt: System prompt from JSON (masterPrompt) - defines AI behavior
        greeting_prompt: Initial greeting from JSON (n61) - what AI says first
    """
    # Fallback greeting if n61 prompt not available
    fallback_greeting = f"Hello, I'm looking for {customer.get('FirstName', 'the account holder')}" if language == "en" else f"Hola, busco a {customer.get('FirstName', 'el titular de la cuenta')}"

    return {
        "type": "Settings",
        "audio": {
            "input": {
                "encoding": "mulaw",
                "sample_rate": 8000,
            },
            "output": {
                "encoding": "mulaw",
                "sample_rate": 8000,
                "container": "none",
            },
        },
        "agent": {
            "language": language,
            "listen": {
                "provider": {
                    "type": "deepgram",
                    "model": "nova-3",
                    "keyterms": [
                        "hello", "goodbye", "hola", "adiós",
                        "español", "spanish", "english", "inglés",
                        "yes", "no", "sí",
                    ]
                }
            },
            "think": {
                "provider": {
                    "type": "open_ai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.7
                },
                "prompt": master_prompt or get_default_prompt(customer, selected_agent, language),
                "functions": get_function_tools(customer)
            },
            "speak": {
                "provider": {
                    "type": "deepgram",
                    "model": selected_agent['voice'].get(language, "aura-asteria-en")
                }
            },
            "greeting": greeting_prompt or fallback_greeting
        }
    }


def get_function_tools(customer: Dict[str, Any]) -> list:
    """
    Get function tools for Deepgram agent.
    Simplified - only essential tools that trigger server-side actions.
    """
    return [
        # Language switching
        {
            "name": "switch_language",
            "description": "Switch conversation language when user requests Spanish or English",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["en", "es"],
                        "description": "Target language: 'en' for English, 'es' for Spanish"
                    }
                },
                "required": ["language"]
            }
        },
        
        # DOB Verification
        {
            "name": "verify_dob",
            "description": "Verify customer's date of birth. Parse spoken date to MM/DD/YYYY format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "parsed_dob": {
                        "type": "string",
                        "description": "Customer's spoken DOB parsed to MM/DD/YYYY format"
                    }
                },
                "required": ["parsed_dob"]
            }
        },
        
        # Process user input through Node Engine
        {
            "name": "process_input",
            "description": "Process customer's response to determine next action. Call this after each substantive customer response.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_input": {
                        "type": "string",
                        "description": "The customer's spoken response"
                    },
                    "current_topic": {
                        "type": "string",
                        "description": "What you're currently discussing (payment, verification, etc.)"
                    }
                },
                "required": ["user_input"]
            }
        },
        
        # Transfer to Level 2
        {
            "name": "transfer_to_level_2",
            "description": "Transfer call to human agent when customer requests or issue is complex",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason for transfer"
                    }
                },
                "required": ["reason"]
            }
        },
        
        # End call
        {
            "name": "end_call",
            "description": "End the call gracefully after business is complete",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason for ending: completed, customer_request, no_answer"
                    }
                },
                "required": ["reason"]
            }
        }
    ]


def get_default_prompt(customer: Dict, agent: Dict, language: str) -> str:
    """Fallback prompt if Node Engine prompt not available"""
    return f"""You are {agent.get('name', 'Sarah')}, a friendly mortgage collections agent.
    
Customer: {customer.get('FirstName', '')} {customer.get('LastName', '')}
Amount Due: ${customer.get('TotalAmountDue', '0')}
Account: ****{customer.get('AccountNumberLastFour', '')}

Your goal is to verify the customer's identity and help them with their payment.
Be professional, empathetic, and helpful.
"""


async def initialize_deepgram_connection(
    customer: Dict,
    selected_agent: Dict,
    language: str,
    master_prompt: str = None,
    greeting_prompt: str = None
):
    """
    Initialize Deepgram connection with agent config.

    Args:
        customer: Customer data
        selected_agent: Agent config
        language: Language code
        master_prompt: System prompt from JSON (masterPrompt) - AI behavior
        greeting_prompt: Greeting from JSON (n61) - what AI says first
    """
    deepgram_ws = await connect_to_deepgram()
    agent_config = get_agent_config(customer, selected_agent, language, master_prompt, greeting_prompt)
    await deepgram_ws.send(json.dumps(agent_config))
    logger.info(f"Voice Agent configured for language: {language}")
    logger.info(f"Greeting: {greeting_prompt[:100] if greeting_prompt else 'fallback'}...")
    return deepgram_ws


# =============================================================================
# MAIN WEBSOCKET HANDLER
# =============================================================================

@outbound_router.websocket("/twilio/{caller_phone}/{phone}")
async def handle_twilio_call(websocket: WebSocket, caller_phone: str, phone: str):
    """
    Main WebSocket handler for Twilio <-> Deepgram <-> Node Engine communication.
    """
    await websocket.accept()
    
    # Connection state
    deepgram_ws = None
    stream_sid = None
    call_sid = None
    current_language = "en"
    reconnect_event = asyncio.Event()
    
    # Customer and agent data
    customer = {}
    selected_agent = {}
    context = {}
    
    try:
        # =================================================================
        # INITIALIZATION: Fetch customer data and initialize Node Engine
        # =================================================================
        
        # Get team and agent
        team = get_team(phone)
        team_id = team.get('team_id')
        client_name = team.get('client_name')
        agents = get_agents(team_id, client_name)
        selected_agent = random.choice(agents)
        
        logger.info(f"Selected agent: {selected_agent.get('name')}")
        
        # Fetch customer data (caller_data is raw API response with PascalCase keys)
        caller_data = await fetch_caller_data(phone)
        if caller_data:
            client_data = await fetch_client_data(caller_data.get('LenderID'))

            # Use caller_data directly (raw API format matches node_engine expected format)
            customer = {
                "FirstName": caller_data.get("FirstName", ""),
                "LastName": caller_data.get("LastName", ""),
                "LoanID": caller_data.get("LoanID", ""),
                "TotalAmountDue": caller_data.get("TotalAmountDue", "0"),
                "MonthlyPayment": caller_data.get("MonthlyPayment", "0"),
                "AccountNumberLastFour": caller_data.get("AccountNumberLastFour", ""),
                "DOB": caller_data.get("DOB", ""),
                "PropertyAddress": caller_data.get("PropertyAddress", ""),
                "RestrictAutoPayDraft": caller_data.get("RestrictAutoPayDraft", "N"),
                "DaysLate": caller_data.get("DaysLate", 0),
                "FeesBalance": caller_data.get("FeesBalance", 0),
                "NextPaymentDueDate": caller_data.get("NextPaymentDueDate", ""),
                "EscrowBalance": caller_data.get("EscrowBalance", 0),
                "PrincipalBalance": caller_data.get("PrincipalBalance", 0),
                "CompanyName": client_data.get("CompanyName", "") if client_data else "",
            }
        else:
            logger.warning(f"No customer data found for {phone}")
            customer = {"FirstName": "Customer", "phone": phone}
        
        # =================================================================
        # INITIALIZE NODE ENGINE CONTEXT
        # =================================================================

        # Build initial context for template rendering
        initial_context = {
            **customer,
            "AgentName": selected_agent.get('name'),
            "AIAgentFullName": selected_agent.get('name'),
            "language": current_language,
            "current_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

        # Get master prompt from JSON (masterPrompt) - defines AI behavior/personality
        master_prompt = node_engine.get_master_prompt(initial_context)
        logger.info(f"Master prompt loaded: {len(master_prompt)} chars")

        # Get greeting prompt from JSON (n61) - what AI says FIRST
        greeting_prompt = node_engine.get_initial_prompt(initial_context)
        logger.info(f"Greeting prompt (n61): {greeting_prompt[:100] if greeting_prompt else 'None'}...")

        # Initialize Deepgram with BOTH prompts:
        # - master_prompt -> think.prompt (LLM system prompt - AI behavior)
        # - greeting_prompt -> greeting (what AI speaks first)
        deepgram_ws = await initialize_deepgram_connection(
            customer,
            selected_agent,
            current_language,
            master_prompt,      # AI behavior/personality from masterPrompt
            greeting_prompt     # What AI says first from n61
        )
        
        # =================================================================
        # ASYNC TASKS
        # =================================================================
        
        async def twilio_receiver():
            """Receive audio from Twilio and forward to Deepgram"""
            nonlocal stream_sid, call_sid, context
            
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event_type = data.get("event")
                    
                    if event_type == "start":
                        stream_sid = data["start"]["streamSid"]
                        call_sid = data["start"]["callSid"]
                        logger.info(f"Stream started: {stream_sid}, Call: {call_sid}")
                        
                        # Initialize Node Engine context now that we have call_sid
                        context = await node_engine.initialize_call(
                            call_sid=call_sid,
                            customer_data=customer,
                            agent_data={
                                "name": selected_agent.get('name'),
                                "id": selected_agent.get('id'),
                            },
                            client_data={
                                "CompanyName": customer.get('CompanyName', ''),
                            }
                        )
                        
                        # Store in active calls
                        active_calls[stream_sid] = {
                            "call_sid": call_sid,
                            "customer": customer,
                            "agent": selected_agent,
                            "messages": [],
                            "context": context,
                        }
                        
                    elif event_type == "media":
                        if deepgram_ws and not reconnect_event.is_set():
                            audio_data = base64.b64decode(data["media"]["payload"])
                            await deepgram_ws.send(audio_data)
                            
                    elif event_type == "stop":
                        logger.info(f"Stream stopped: {stream_sid}")
                        break
                        
            except WebSocketDisconnect:
                logger.info("Twilio WebSocket disconnected")
            except Exception as e:
                logger.error(f"Error in twilio_receiver: {e}")
        
        async def deepgram_sender():
            """Keep-alive for Deepgram connection"""
            try:
                while True:
                    await asyncio.sleep(30)
                    if deepgram_ws and not reconnect_event.is_set():
                        await deepgram_ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception as e:
                logger.error(f"Error in deepgram_sender: {e}")
        
        async def deepgram_receiver():
            """Receive events from Deepgram and handle function calls"""
            nonlocal deepgram_ws, current_language, context
            
            try:
                async for message in deepgram_ws:
                    # Text messages (JSON events)
                    if isinstance(message, str):
                        data = json.loads(message)
                        event_type = data.get("type")

                        # Log ALL Deepgram events
                        if event_type not in ["Audio"]:  # Skip audio events (too noisy)
                            logger.info(f"📡 [DEEPGRAM EVENT] {event_type}")

                        # =================================================
                        # FUNCTION CALL HANDLING
                        # =================================================
                        if event_type == "FunctionCall":
                            func = data.get("function", {})
                            function_name = func.get("name")
                            function_call_id = func.get("id")
                            arguments = json.loads(func.get("arguments", "{}"))
                            
                            logger.info(f"Function call: {function_name} with {arguments}")
                            
                            response_content = ""
                            update_prompt = None
                            
                            # ----- SWITCH LANGUAGE -----
                            if function_name == "switch_language":
                                target_lang = arguments.get("language", "en")
                                
                                if target_lang != current_language:
                                    logger.info(f"Switching language: {current_language} -> {target_lang}")
                                    current_language = target_lang
                                    
                                    # Update context
                                    await context_manager.update_context(call_sid, {"language": target_lang})
                                    
                                    # Reconnect Deepgram with new language
                                    reconnect_event.set()
                                    await deepgram_ws.close()

                                    # Get updated prompts with new language context
                                    current_node = await context_manager.get_current_node(call_sid)
                                    ctx = await context_manager.get_context(call_sid)

                                    # Re-render master prompt with new language
                                    new_master_prompt = node_engine.get_master_prompt(ctx)
                                    # Get current node's prompt as greeting (since we're mid-call)
                                    new_node_prompt = node_engine.get_rendered_prompt(current_node, ctx)

                                    deepgram_ws = await initialize_deepgram_connection(
                                        customer, selected_agent, target_lang,
                                        new_master_prompt,  # AI behavior
                                        new_node_prompt     # Current node prompt as greeting
                                    )
                                    reconnect_event.clear()
                                    
                                    response_content = "Ahora hablaré en español." if target_lang == "es" else "I'll now speak in English."
                                else:
                                    response_content = "Already speaking in the requested language."
                            
                            # ----- VERIFY DOB -----
                            elif function_name == "verify_dob":
                                parsed_dob = arguments.get("parsed_dob", "")
                                expected_dob = customer.get("DOB", "")
                                
                                # Normalize DOB formats for comparison
                                dob_match = normalize_dob(parsed_dob) == normalize_dob(expected_dob)
                                
                                # Increment attempt counter
                                attempts = await context_manager.increment_counter(call_sid, "dob_attempts")
                                
                                if dob_match:
                                    # DOB verified - process through Node Engine
                                    result = await node_engine.process(
                                        call_sid=call_sid,
                                        node_id=await context_manager.get_current_node(call_sid),
                                        user_input=f"DOB verified: {parsed_dob}",
                                        context=await context_manager.get_context(call_sid)
                                    )
                                    
                                    await context_manager.update_context(call_sid, {"dob_verified": True})
                                    
                                    if result.get("should_update_agent") and result.get("prompt"):
                                        update_prompt = result["prompt"]
                                    
                                    response_content = "Thank you for verifying your date of birth. I also need to share an important disclosure with you."
                                else:
                                    await context_manager.update_context(call_sid, {"dob_mismatch": True})
                                    
                                    if attempts >= 2:
                                        response_content = "I'm sorry, but I wasn't able to verify your identity. For security purposes, I'll need to transfer you to a specialist."
                                    else:
                                        response_content = "I'm sorry, that doesn't match our records. Could you please repeat your date of birth?"
                            
                            # ----- PROCESS INPUT (NODE ENGINE) -----
                            elif function_name == "process_input":
                                user_input = arguments.get("user_input", "")
                                current_node = await context_manager.get_current_node(call_sid)
                                ctx = await context_manager.get_context(call_sid)
                                
                                # Process through Node Engine
                                result = await node_engine.process(
                                    call_sid=call_sid,
                                    node_id=current_node,
                                    user_input=user_input,
                                    context=ctx
                                )
                                
                                logger.info(f"Node Engine result: {result.get('next_node')}, update: {result.get('should_update_agent')}")
                                
                                if result.get("should_update_agent") and result.get("prompt"):
                                    update_prompt = result["prompt"]
                                
                                # Check for end of call
                                if result.get("next_node") == "END":
                                    response_content = "Thank you for calling. Have a great day!"
                                    # Will trigger call end
                                else:
                                    response_content = "I understand."
                            
                            # ----- TRANSFER TO LEVEL 2 -----
                            elif function_name == "transfer_to_level_2":
                                reason = arguments.get("reason", "customer_request")
                                
                                await context_manager.update_context(call_sid, {
                                    "transfer_requested": True,
                                    "transfer_reason": reason
                                })
                                
                                response_content = "I'll transfer you to a specialist who can better assist you. Please hold."
                                
                                # Trigger actual transfer
                                asyncio.create_task(execute_transfer(call_sid, TRANSFER_PHONE_NUMBER))
                            
                            # ----- END CALL -----
                            elif function_name == "end_call":
                                reason = arguments.get("reason", "completed")
                                
                                await context_manager.update_context(call_sid, {
                                    "call_ended": True,
                                    "end_reason": reason
                                })
                                
                                response_content = "Thank you for calling. Goodbye!"
                            
                            # Send function response
                            function_response = {
                                "type": "FunctionCallResponse",
                                "id": function_call_id,
                                "name": function_name,
                                "content": response_content
                            }
                            await deepgram_ws.send(json.dumps(function_response))
                            
                            # Update prompt if needed
                            if update_prompt:
                                prompt_update = {
                                    "type": "UpdatePrompt",
                                    "prompt": update_prompt
                                }
                                await deepgram_ws.send(json.dumps(prompt_update))
                                logger.info(f"Prompt updated for new node")

                                # Inject agent message to trigger LLM to generate response based on new prompt
                                # Note: V1 API uses "content" instead of "message"
                                inject_message = {
                                    "type": "InjectAgentMessage",
                                    "content": "Please continue with the next step."
                                }
                                await deepgram_ws.send(json.dumps(inject_message))
                                logger.info(f"InjectAgentMessage sent to trigger agent response")
                        
                        # =================================================
                        # CONVERSATION TEXT LOGGING
                        # =================================================
                        elif event_type == "ConversationText":
                            role = data.get("role", "")
                            content = data.get("content", "")

                            # ========== DETAILED CONVERSATION LOG ==========
                            if role == "assistant":
                                logger.info(f"🤖 AGENT SAYS: {content}")
                            elif role == "user":
                                logger.info(f"👤 USER SAYS: {content}")
                            else:
                                logger.info(f"[{role}] [{current_language}]: {content}")

                            # Store in active calls and context
                            if stream_sid and stream_sid in active_calls:
                                active_calls[stream_sid]["messages"].append({
                                    "role": role,
                                    "content": content,
                                    "language": current_language,
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                })

                            # Append to context transcript (only if context exists)
                            # Note: Early Deepgram events may arrive before Twilio "start" creates context
                            if call_sid and context:
                                await context_manager.append_transcript(call_sid, role, content)

                            # =================================================
                            # AUTO-PROCESS USER INPUT THROUGH NODE ENGINE
                            # =================================================
                            # Process every user message to extract variables and trigger transitions
                            if role == "user" and call_sid and context:
                                try:
                                    current_node = await context_manager.get_current_node(call_sid)
                                    ctx = await context_manager.get_context(call_sid)

                                    # Safety check - context must exist
                                    if not ctx:
                                        logger.warning(f"⚠️ [NODE ENGINE] Context not ready for {call_sid}, skipping processing")
                                        continue

                                    logger.info(f"📍 [NODE ENGINE] Processing user input at node: {current_node}")
                                    logger.info(f"📍 [NODE ENGINE] User said: '{content}'")

                                    # Process through Node Engine (extracts variables, determines next node)
                                    result = await node_engine.process(
                                        call_sid=call_sid,
                                        node_id=current_node,
                                        user_input=content,
                                        context=ctx
                                    )

                                    logger.info(f"📍 [NODE ENGINE] Result: {current_node} -> {result.get('next_node')}")
                                    logger.info(f"📍 [NODE ENGINE] Should update agent: {result.get('should_update_agent')}")

                                    # Update prompt if node changed
                                    if result.get("should_update_agent") and result.get("prompt"):
                                        prompt_update = {
                                            "type": "UpdatePrompt",
                                            "prompt": result["prompt"]
                                        }
                                        await deepgram_ws.send(json.dumps(prompt_update))
                                        logger.info(f"✅ [PROMPT UPDATED] New node: {result.get('next_node')}")
                                        logger.info(f"✅ [PROMPT CONTENT] {result['prompt'][:200]}...")

                                        # Trigger agent to respond with new prompt
                                        inject_message = {
                                            "type": "InjectAgentMessage",
                                            "content": "Continue with your current task."
                                        }
                                        await deepgram_ws.send(json.dumps(inject_message))
                                        logger.info(f"✅ [INJECT] Triggered fresh response for new node")

                                        # Update local context reference
                                        context = result.get("context", context)
                                    else:
                                        logger.info(f"⏸️ [NO TRANSITION] Staying at node: {current_node}")

                                except Exception as e:
                                    logger.error(f"❌ [NODE ENGINE ERROR] {e}", exc_info=True)

                        # =================================================
                        # USER BARGE-IN
                        # =================================================
                        elif event_type == "UserStartedSpeaking":
                            logger.info("User started speaking (barge-in)")
                            if stream_sid:
                                await websocket.send_json({
                                    "event": "clear",
                                    "streamSid": stream_sid
                                })
                        
                        # =================================================
                        # ERROR HANDLING
                        # =================================================
                        elif event_type == "Error":
                            error_msg = data.get("description", "Unknown error")
                            error_code = data.get("code", "N/A")
                            logger.error(f"Deepgram Error [{error_code}]: {error_msg}")
                    
                    # =================================================
                    # BINARY MESSAGES (TTS AUDIO)
                    # =================================================
                    elif isinstance(message, bytes):
                        if not reconnect_event.is_set() and stream_sid:
                            audio_base64 = base64.b64encode(message).decode("utf-8")
                            await websocket.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_base64}
                            })
                            
            except websockets.exceptions.ConnectionClosed:
                logger.info("Deepgram connection closed")
            except Exception as e:
                logger.error(f"Error in deepgram_receiver: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        # Run all tasks concurrently
        await asyncio.gather(
            twilio_receiver(),
            deepgram_sender(),
            deepgram_receiver()
        )
        
    except Exception as e:
        logger.error(f"Error handling call: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
    finally:
        # =================================================================
        # CLEANUP: Generate memo and close connections
        # =================================================================
        
        if call_sid:
            logger.info(f"Starting cleanup for call {call_sid}")
            
            try:
                # Get recording SID
                recording_sid = recording_mappings.get(call_sid)
                if recording_sid:
                    await context_manager.update_context(call_sid, {"recording_sid": recording_sid})
                
                # Get final context
                final_context = await node_engine.end_call(call_sid)
                
                # Get conversation history
                conversation_history = []
                if stream_sid and stream_sid in active_calls:
                    conversation_history = active_calls[stream_sid].get("messages", [])
                
                # Build and post memo
                memo_data = MemoBuilder.build_memo_from_context(
                    context=final_context,
                    conversation_history=conversation_history
                )
                
                logger.info(f"[MEMO] Generated memo with {len(memo_data)} fields")
                
                result = await post_memo(memo_data)
                if result:
                    logger.info("[MEMO] Posted successfully")
                else:
                    logger.error("[MEMO] Posting failed")
                    
            except Exception as e:
                logger.error(f"[MEMO] Error: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        # Close Deepgram
        if deepgram_ws:
            await deepgram_ws.close()
            logger.info("Deepgram connection closed")
        
        # Cleanup active calls
        if stream_sid and stream_sid in active_calls:
            del active_calls[stream_sid]
            logger.info(f"Removed call {stream_sid} from active calls")
        
        # Cleanup recording mapping
        if call_sid and call_sid in recording_mappings:
            del recording_mappings[call_sid]


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def normalize_dob(dob_string: str) -> str:
    """Normalize DOB to MM/DD/YYYY format for comparison"""
    if not dob_string:
        return ""
    
    # Remove any non-alphanumeric except slashes and dashes
    import re
    cleaned = re.sub(r'[^\d/\-]', '', dob_string)
    
    # Try different formats
    formats = [
        "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", 
        "%d/%m/%Y", "%m/%d/%y", "%Y/%m/%d"
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%m/%d/%Y")
        except ValueError:
            continue
    
    return cleaned


async def execute_transfer(call_sid: str, transfer_number: str):
    """Execute call transfer via Twilio"""
    try:
        twilio_client.calls(call_sid).update(
            url=f"{SERVER_URL}/outbound/transfer_twiml?phone={quote(transfer_number)}",
            method="POST"
        )
        logger.info(f"Transfer initiated for {call_sid} to {transfer_number}")
    except Exception as e:
        logger.error(f"Transfer failed: {e}")