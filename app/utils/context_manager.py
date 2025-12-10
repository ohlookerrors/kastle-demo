"""
Context Manager - Thread-safe per-call context management for concurrent calls.

Each call gets an isolated context that is protected by async locks.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ContextManager:
    """
    Manages context for multiple concurrent calls.
    
    Each call_sid gets its own isolated context dictionary protected by an async lock.
    This ensures thread-safe operations when multiple calls are being processed.
    """
    
    def __init__(self):
        self._contexts: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
    
    async def create_context(self, call_sid: str, initial_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new context for a call.
        
        Args:
            call_sid: Unique identifier for the call
            initial_data: Customer/agent/client data to initialize context
            
        Returns:
            The newly created context dictionary
        """
        async with self._global_lock:
            now = datetime.now(timezone.utc)
            
            self._contexts[call_sid] = {
                # Call metadata
                "call_sid": call_sid,
                "created_at": now.isoformat(),
                "current_node": "n61",  # Starting node (greeting)
                "language": "en",
                
                # Runtime data
                "current_date": now.strftime("%Y-%m-%d"),
                "current_day_of_week": now.strftime("%A"),
                "current_time": now.strftime("%I:%M %p"),
                
                # Transcript storage
                "transcript": [],
                
                # Extracted variables (will be populated during call)
                "extracted_dob": None,
                "party_name": None,
                "dob_verified": False,
                "dob_attempts": 0,
                "mini_miranda_complete": False,
                "occupancy_verified": False,
                "occupancy_status": None,
                "affected_by_disaster": None,
                "payment_date_received": False,
                "payment_amount_received": False,
                "user_provided_payment_amount": None,
                "upd_extracted_payment_date": None,
                "new_routing_number": "N/A",
                "new_account_number": "N/A",
                "new_account_payment_method": None,
                "existing_bank_account_confirmed": False,
                "new_bank_account_confirmed": False,
                "new_routing_number_confirmed": False,
                "declined_bank_account_setup_today": False,
                "user_claims_payment_made": False,
                "user_wants_set_up_later": False,
                "borrower_wants_options": False,
                "nacha_permission_granted": False,
                "payment_processed": False,
                "confirmation_number": None,
                "transfer_completed": False,
                "transfer_reason": None,
                "callback_scheduled": False,
                "callback_time": None,
                "appt_scheduled_success": False,
                "delinquency_reason": None,
                
                # API response tracking
                "api_status_code": None,
                "api_error": None,
                
                # Merge in provided initial data (customer, agent, client info)
                **initial_data
            }
            
            self._locks[call_sid] = asyncio.Lock()
            logger.info(f"[{call_sid}] Context created for {initial_data.get('FirstName', 'Unknown')}")
            
        return self._contexts[call_sid].copy()
    
    async def get_context(self, call_sid: str) -> Optional[Dict[str, Any]]:
        """
        Get a copy of the context for a call (thread-safe).
        
        Args:
            call_sid: Unique identifier for the call
            
        Returns:
            Copy of context dictionary or None if not found
        """
        if call_sid not in self._contexts:
            logger.warning(f"[{call_sid}] Context not found")
            return None
            
        async with self._locks[call_sid]:
            return self._contexts[call_sid].copy()
    
    async def update_context(self, call_sid: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Update context with new values (thread-safe).
        
        Args:
            call_sid: Unique identifier for the call
            updates: Dictionary of key-value pairs to update
            
        Returns:
            Updated context copy or None if not found
        """
        if call_sid not in self._contexts:
            logger.warning(f"[{call_sid}] Cannot update - context not found")
            return None
            
        async with self._locks[call_sid]:
            # Filter out None values to avoid overwriting with None
            filtered_updates = {k: v for k, v in updates.items() if v is not None}
            self._contexts[call_sid].update(filtered_updates)
            
            if filtered_updates:
                logger.debug(f"[{call_sid}] Context updated: {list(filtered_updates.keys())}")
                
            return self._contexts[call_sid].copy()
    
    async def append_transcript(self, call_sid: str, role: str, content: str) -> None:
        """
        Append a message to the call transcript.
        
        Args:
            call_sid: Unique identifier for the call
            role: 'user' or 'assistant'
            content: The message content
        """
        if call_sid not in self._contexts:
            # This is expected during early connection - Deepgram sends events before Twilio "start"
            logger.debug(f"[{call_sid}] Skipping transcript - context not yet created")
            return
            
        async with self._locks[call_sid]:
            self._contexts[call_sid]["transcript"].append({
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
    
    async def get_transcript(self, call_sid: str, last_n: int = None) -> List[Dict]:
        """
        Get the conversation transcript.
        
        Args:
            call_sid: Unique identifier for the call
            last_n: Optional limit to get only last N messages
            
        Returns:
            List of transcript messages
        """
        if call_sid not in self._contexts:
            return []
            
        async with self._locks[call_sid]:
            transcript = self._contexts[call_sid].get("transcript", [])
            if last_n:
                return transcript[-last_n:]
            return transcript.copy()
    
    async def set_current_node(self, call_sid: str, node_id: str) -> None:
        """
        Set the current node for the call.
        
        Args:
            call_sid: Unique identifier for the call
            node_id: Node identifier (e.g., 'n49')
        """
        if call_sid not in self._contexts:
            return
            
        async with self._locks[call_sid]:
            old_node = self._contexts[call_sid].get("current_node")
            self._contexts[call_sid]["current_node"] = node_id
            logger.info(f"[{call_sid}] Node changed: {old_node} -> {node_id}")
    
    async def get_current_node(self, call_sid: str) -> str:
        """
        Get the current node for the call.
        
        Args:
            call_sid: Unique identifier for the call
            
        Returns:
            Current node ID or 'n61' (default starting node)
        """
        if call_sid not in self._contexts:
            return "n61"
            
        async with self._locks[call_sid]:
            return self._contexts[call_sid].get("current_node", "n61")
    
    async def increment_counter(self, call_sid: str, counter_name: str) -> int:
        """
        Increment a counter in context (e.g., dob_attempts).
        
        Args:
            call_sid: Unique identifier for the call
            counter_name: Name of the counter field
            
        Returns:
            New counter value
        """
        if call_sid not in self._contexts:
            return 0
            
        async with self._locks[call_sid]:
            current = self._contexts[call_sid].get(counter_name, 0)
            self._contexts[call_sid][counter_name] = current + 1
            return current + 1
    
    async def delete_context(self, call_sid: str) -> Optional[Dict[str, Any]]:
        """
        Delete context when call ends and return final state.
        
        Args:
            call_sid: Unique identifier for the call
            
        Returns:
            Final context state before deletion (for memo building)
        """
        async with self._global_lock:
            final_context = None
            
            if call_sid in self._contexts:
                final_context = self._contexts[call_sid].copy()
                del self._contexts[call_sid]
                logger.info(f"[{call_sid}] Context deleted")
                
            if call_sid in self._locks:
                del self._locks[call_sid]
                
            return final_context
    
    async def get_all_active_calls(self) -> List[str]:
        """
        Get list of all active call_sids.
        
        Returns:
            List of active call_sid strings
        """
        async with self._global_lock:
            return list(self._contexts.keys())
    
    async def get_context_summary(self, call_sid: str) -> Dict[str, Any]:
        """
        Get a summary of key context fields (for logging/debugging).
        
        Args:
            call_sid: Unique identifier for the call
            
        Returns:
            Summary dictionary
        """
        if call_sid not in self._contexts:
            return {}
            
        async with self._locks[call_sid]:
            ctx = self._contexts[call_sid]
            return {
                "call_sid": call_sid,
                "current_node": ctx.get("current_node"),
                "customer": f"{ctx.get('FirstName', '')} {ctx.get('LastName', '')}",
                "language": ctx.get("language"),
                "payment_amount": ctx.get("user_provided_payment_amount"),
                "payment_date": ctx.get("upd_extracted_payment_date"),
                "payment_processed": ctx.get("payment_processed"),
                "transcript_count": len(ctx.get("transcript", []))
            }


# Singleton instance for use across the application
context_manager = ContextManager()