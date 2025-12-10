"""
Node Engine - Core engine for processing conversation nodes.

This replaces the coordinator.py and all service files.
Single engine handles all 47 nodes generically.
"""

import json
import logging
import httpx
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from openai import AsyncAzureOpenAI

from app.utils.context_manager import context_manager
from app.utils.template_render import render_template, substitute_api_body
from app.utils.transition_rules import get_next_node, get_node_description

logger = logging.getLogger(__name__)



class NodeEngine:
    """
    Core engine that processes all conversation nodes.
    
    Singleton pattern - one instance handles all concurrent calls.
    Each call has its own isolated context via ContextManager.
    """
    
    def __init__(self, nodes_path: str = "outbound_config.json"):
        """
        Initialize the node engine.

        Args:
            nodes_path: Path to the JSON file containing node definitions
        """
        self.config_data = self._load_config(nodes_path)
        self.master_prompt = self.config_data.get("masterPrompt", "")
        self.nodes = self._extract_nodes(self.config_data)
        self.llm_client = self._init_llm_client()
        self.context_manager = context_manager  # Use singleton instance

        logger.info(f"NodeEngine initialized with {len(self.nodes)} nodes")

    def _load_config(self, nodes_path: str) -> Dict[str, Any]:
        """Load full config from JSON file."""
        try:
            with open(nodes_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config from {nodes_path}: {e}")
            return {}

    def _extract_nodes(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract nodes from config data."""
        # Filter out non-node keys (masterPrompt, knowledgeBase, etc.)
        non_node_keys = {"masterPrompt", "knowledgeBase", "version", "metadata"}
        return {k: v for k, v in data.items() if k not in non_node_keys and isinstance(v, dict) and "details" in v}
    
    def get_master_prompt(self, context: Dict[str, Any]) -> str:
        """
        Get the rendered master prompt (system prompt for LLM).

        Args:
            context: Context for variable substitution

        Returns:
            Rendered master prompt string
        """
        if not self.master_prompt:
            logger.warning("No masterPrompt found in config")
            return ""
        return render_template(self.master_prompt, context)
    
    def _init_llm_client(self) -> AsyncAzureOpenAI:
        """Initialize Azure OpenAI client for variable extraction."""
        return AsyncAzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
    
    async def initialize_call(
        self,
        call_sid: str,
        customer_data: Dict[str, Any],
        agent_data: Dict[str, Any],
        client_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Initialize context for a new call.
        
        Args:
            call_sid: Unique call identifier
            customer_data: Data from customer API (get_outbound_data)
            agent_data: Selected agent info
            client_data: Client/lender info
            
        Returns:
            Initialized context dictionary
        """
        initial_data = {
            # Customer data
            "LoanID": customer_data.get("LoanID"),
            "FirstName": customer_data.get("FirstName"),
            "LastName": customer_data.get("LastName"),
            "TotalAmountDue": customer_data.get("TotalAmountDue"),
            "MonthlyPayment": customer_data.get("MonthlyPayment"),
            "AccountNumberLastFour": customer_data.get("AccountNumberLastFour"),
            "DOB": customer_data.get("DOB"),
            "PropertyAddress": customer_data.get("PropertyAddress"),
            "RestrictAutoPayDraft": customer_data.get("RestrictAutoPayDraft", "N"),
            "DaysLate": customer_data.get("DaysLate", 0),
            "FeesBalance": customer_data.get("FeesBalance", 0),
            "NextPaymentDueDate": customer_data.get("NextPaymentDueDate"),
            "EscrowBalance": customer_data.get("EscrowBalance"),
            "PrincipalBalance": customer_data.get("PrincipalBalance"),
            
            # Agent data
            "AIAgentFullName": agent_data.get("name"),
            "AgentName": agent_data.get("name"),
            "agent_id": agent_data.get("id"),
            
            # Client data
            "CompanyName": client_data.get("CompanyName"),
            "LenderID": client_data.get("LenderID"),
        }
        
        context = await self.context_manager.create_context(call_sid, initial_data)
        logger.info(f"[{call_sid}] Call initialized for {initial_data.get('FirstName')} {initial_data.get('LastName')}")
        
        return context
    
    async def process(
        self,
        call_sid: str,
        node_id: str,
        user_input: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Main processing function - handles a conversation turn.
        
        Args:
            call_sid: Unique call identifier
            node_id: Current node ID
            user_input: User's transcript from Deepgram
            context: Current context dictionary
            
        Returns:
            {
                "next_node": str,
                "prompt": str or None,
                "context": Dict,
                "should_update_agent": bool
            }
        """
        logger.info(f"🔄 [{call_sid}] PROCESSING NODE: {node_id}")
        logger.info(f"🔄 [{call_sid}] USER INPUT: '{user_input}'")

        # NOTE: Transcript is already appended in outbound_call.py ConversationText handler
        # Do NOT append again here to avoid duplicates

        # 2. Get recent transcript for extraction context
        transcript = await self.context_manager.get_transcript(call_sid, last_n=10)
        transcript_text = self._format_transcript(transcript)
        logger.info(f"📜 [{call_sid}] TRANSCRIPT:\n{transcript_text}")

        # 3. Extract variables from conversation
        try:
            extracted = await self.extract_variables(node_id, transcript_text, context)
            logger.info(f"🔍 [{call_sid}] EXTRACTED VARIABLES: {extracted}")
        except Exception as e:
            logger.error(f"❌ [{call_sid}] Variable extraction failed: {e}", exc_info=True)
            extracted = {}

        # 4. DOB Verification Logic - Compare extracted DOB with DOB on file
        if node_id == "n68" and extracted.get("extracted_dob"):
            extracted_dob = extracted.get("extracted_dob")
            dob_on_file = context.get("DOB", "")

            logger.info(f"🔍 [{call_sid}] DOB Comparison: extracted='{extracted_dob}' vs on_file='{dob_on_file}'")

            # Normalize DOB formats for comparison (handle various formats)
            def normalize_dob(dob_str):
                if not dob_str:
                    return None
                # Remove any non-alphanumeric characters and convert to standard format
                import re
                clean = re.sub(r'[^0-9]', '', str(dob_str))
                # Try to parse as YYYYMMDD or MMDDYYYY
                if len(clean) == 8:
                    return clean
                return dob_str.strip().lower()

            normalized_extracted = normalize_dob(extracted_dob)
            normalized_on_file = normalize_dob(dob_on_file)

            if normalized_extracted and normalized_on_file:
                if normalized_extracted == normalized_on_file:
                    extracted["dob_verified"] = True
                    extracted["dob_correct"] = True
                    logger.info(f"✅ [{call_sid}] DOB VERIFIED - Match!")
                else:
                    extracted["dob_mismatch"] = True
                    extracted["dob_incorrect"] = True
                    logger.info(f"❌ [{call_sid}] DOB MISMATCH - extracted: {extracted_dob}, on_file: {dob_on_file}")

        # 4.5. Sync payment date variable names for template compatibility
        # n67 extracts user_provided_payment_date, but templates use upd_extracted_payment_date
        if extracted.get("user_provided_payment_date") and extracted.get("user_provided_payment_date") not in ["NA", "N/A", None, ""]:
            extracted["upd_extracted_payment_date"] = extracted["user_provided_payment_date"]
            logger.info(f"📅 [{call_sid}] Synced payment date: {extracted['upd_extracted_payment_date']}")

        # 5. Update context with extracted variables
        context = await self.context_manager.update_context(call_sid, extracted)

        # 6. Determine next node
        next_node = get_next_node(node_id, extracted, context)
        logger.info(f"➡️ [{call_sid}] TRANSITION: {node_id} -> {next_node} ({get_node_description(next_node)})")
        
        # 6. Check if call should end
        if next_node == "END":
            return {
                "next_node": "END",
                "prompt": None,
                "context": context,
                "should_update_agent": False
            }
        
        # 7. Execute APIs if transitioning to a new node that has APIs
        if next_node != node_id:
            node_config = self.nodes.get(next_node, {})
            apis = node_config.get('details', {}).get('apis', [])
            if apis:
                logger.info(f"[{call_sid}] Executing APIs for node {next_node}")
                context = await self.execute_apis(next_node, context)
                await self.context_manager.update_context(call_sid, context)
        
        # 8. Get rendered prompt for next node (only if node changed)
        prompt = None
        if next_node != node_id:
            # Debug: Log key context variables for template rendering
            logger.info(f"📋 [{call_sid}] CONTEXT FOR PROMPT RENDER: AccountNumberLastFour={context.get('AccountNumberLastFour')}, upd_extracted_payment_date={context.get('upd_extracted_payment_date')}, user_provided_payment_date={context.get('user_provided_payment_date')}, RestrictAutoPayDraft={context.get('RestrictAutoPayDraft')}")

            prompt = self.get_rendered_prompt(next_node, context)
            await self.context_manager.set_current_node(call_sid, next_node)

            # Add assistant response to transcript (the prompt we're giving)
            if prompt:
                await self.context_manager.append_transcript(call_sid, "assistant", f"[Node: {next_node}]")
        
        return {
            "next_node": next_node,
            "prompt": prompt,
            "context": context,
            "should_update_agent": next_node != node_id
        }
    
    async def extract_variables(
        self,
        node_id: str,
        transcript: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Use LLM to extract variables from conversation.
        
        Args:
            node_id: Current node ID
            transcript: Recent conversation transcript
            context: Current context
            
        Returns:
            Dictionary of extracted variable values
        """
        node = self.nodes.get(node_id, {})
        variables = node.get('details', {}).get('variables', [])
        
        if not variables:
            return {}
        
        # Build variable descriptions for LLM
        var_descriptions = []
        for var in variables:
            var_descriptions.append({
                "name": var.get("name"),
                "type": var.get("type", "string"),
                "description": var.get("description", "")
            })
        
        # Build extraction prompt
        extraction_prompt = f"""Extract variables from the USER's messages in this transcript.

<transcript>
{transcript}
</transcript>

<variables_to_extract>
{json.dumps(var_descriptions, indent=2)}
</variables_to_extract>

<reference_info>
Customer name on file: {context.get('FirstName')} {context.get('LastName')}
</reference_info>

<critical_instructions>
- ONLY extract values that the USER explicitly stated in their messages
- DO NOT extract or guess values from context or reference info
- DO NOT hallucinate or infer values that weren't clearly spoken by the user
- If user did not provide a date of birth, extracted_dob should be null
- If user did not confirm something, the boolean should be false
- Return ONLY a valid JSON object with variable names as keys
- For boolean variables, use true/false (not strings)
- For dates that user DID provide, use YYYY-MM-DD format
- For string variables, use null if NOT explicitly stated by user
</critical_instructions>

Return the JSON object:"""

        try:
            response = await self.llm_client.chat.completions.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
                messages=[
                    {
                        "role": "system",
                        "content": "You are a variable extraction assistant. Return only valid JSON with no additional text."
                    },
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Clean up results - remove null values and "N/A" strings
            cleaned = {}
            for key, value in result.items():
                if value is not None and value != "N/A" and value != "null":
                    cleaned[key] = value
            
            return cleaned
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return {}
        except Exception as e:
            logger.error(f"LLM extraction error: {e}")
            return {}
    
    async def execute_apis(
        self,
        node_id: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute APIs defined in a node.
        
        Args:
            node_id: Node ID with API definitions
            context: Current context
            
        Returns:
            Updated context with API response data
        """
        node = self.nodes.get(node_id, {})
        apis = node.get('details', {}).get('apis', [])
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for api in apis:
                try:
                    if api.get('post'):
                        # POST request
                        url = api['post']
                        
                        # Build request body
                        body_items = api.get('body', [])
                        body = substitute_api_body(body_items, context)
                        
                        logger.info(f"API POST to {url}: {body}")
                        
                        response = await client.post(
                            url,
                            json=body,
                            headers={"Content-Type": "application/json"}
                        )
                        
                        context['api_status_code'] = response.status_code
                        
                        if response.status_code == 200:
                            response_data = response.json()
                            
                            # Map response data to context
                            for resp_item in api.get('response_data', []):
                                key = resp_item.get('key')
                                path = resp_item.get('path', key)
                                
                                # Handle nested paths
                                value = response_data
                                for part in path.split('.'):
                                    value = value.get(part) if isinstance(value, dict) else None
                                    if value is None:
                                        break
                                
                                if value is not None:
                                    context[key] = value
                                    
                            logger.info(f"API success: {response_data}")
                        else:
                            context['api_error'] = f"Status {response.status_code}: {response.text}"
                            logger.error(f"API failed: {context['api_error']}")
                    
                    elif api.get('get'):
                        # GET request
                        url = render_template(api['get'], context)
                        
                        logger.info(f"API GET: {url}")
                        
                        response = await client.get(url)
                        context['api_status_code'] = response.status_code
                        
                        if response.status_code == 200:
                            context['api_response'] = response.json()
                            logger.info(f"API success: {context['api_response']}")
                        else:
                            context['api_error'] = f"Status {response.status_code}"
                            
                except Exception as e:
                    logger.error(f"API execution error: {e}")
                    context['api_error'] = str(e)
        
        return context
    
    def get_rendered_prompt(
        self,
        node_id: str,
        context: Dict[str, Any]
    ) -> Optional[str]:
        """
        Get fully rendered prompt for a node.
        
        Args:
            node_id: Node ID
            context: Current context for variable substitution
            
        Returns:
            Rendered prompt string or None
        """
        node = self.nodes.get(node_id, {})
        prompt_template = node.get('details', {}).get('prompt', '')

        # Handle nested prompt structure: {"prompt": {"prompt": "..."}}
        if isinstance(prompt_template, dict):
            prompt_template = prompt_template.get('prompt', '')

        if not prompt_template:
            logger.warning(f"No prompt found for node {node_id}")
            return None

        # Render template with context
        rendered = render_template(prompt_template, context)
        
        return rendered
    
    def get_initial_prompt(self, context: Dict[str, Any]) -> str:
        """
        Get the initial prompt for starting a call (n61 - greeting).
        
        Args:
            context: Initial context
            
        Returns:
            Rendered greeting prompt
        """
        return self.get_rendered_prompt("n61", context)
    
    async def end_call(self, call_sid: str) -> Dict[str, Any]:
        """
        End a call and return final context for memo building.
        
        Args:
            call_sid: Call identifier
            
        Returns:
            Final context dictionary
        """
        logger.info(f"[{call_sid}] Ending call")
        
        # Get final context before deletion
        final_context = await self.context_manager.delete_context(call_sid)
        
        return final_context or {}
    
    def _format_transcript(self, transcript: List[Dict]) -> str:
        """Format transcript list into readable string."""
        lines = []
        for entry in transcript:
            role = entry.get('role', 'unknown')
            content = entry.get('content', '')
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
    
    def get_node_info(self, node_id: str) -> Dict[str, Any]:
        """
        Get information about a node.
        
        Args:
            node_id: Node identifier
            
        Returns:
            Node configuration dictionary
        """
        return self.nodes.get(node_id, {})
    
    def list_all_nodes(self) -> List[str]:
        """Get list of all available node IDs."""
        return list(self.nodes.keys())


# Create singleton instance
_engine_instance: Optional[NodeEngine] = None


def get_node_engine(nodes_path: str = "outbound_config.json") -> NodeEngine:
    """
    Get or create the singleton NodeEngine instance.
    
    Args:
        nodes_path: Path to nodes JSON file
    Returns:
        NodeEngine instance
    """
    global _engine_instance
    
    if _engine_instance is None:
        _engine_instance = NodeEngine(nodes_path)
    
    return _engine_instance