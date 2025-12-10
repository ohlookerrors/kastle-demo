"""
Memo Builder - Build memo data from Node Engine context.

This module extracts all relevant data from the call context and
builds a memo structure for posting to the FICS API.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
import logging

logger = logging.getLogger(__name__)


class MemoBuilder:
    """
    Build memo data from Node Engine context.
    
    The context contains all collected data from the conversation,
    including payment info, customer data, and extracted variables.
    """
    
    # Subject lines for different service types
    SUBJECT_MAP = {
        "payment_now": "Payment Collected - Collections Call",
        "schedule_payment": "Payment Scheduled - Collections Call",
        "promise_to_pay": "Promise to Pay Recorded",
        "payment_already_made": "Payment Verification - Collections Call",
        "callback": "Callback Scheduled",
        "transfer": "Transferred to Level 2 - Collections Call",
        "appointment": "Appointment Scheduled - Loss Mitigation",
        "disaster": "Disaster Impact Recorded",
        "contact_made": "Customer Contact - Outbound Collections",
    }
    
    # Disposition values for different service types
    DISPOSITION_MAP = {
        "payment_now": "Payment Processed",
        "schedule_payment": "Payment Scheduled",
        "promise_to_pay": "Promise to Pay",
        "payment_already_made": "Payment Verified",
        "callback": "Callback Scheduled",
        "transfer": "Transferred to Level 2",
        "appointment": "Appointment Scheduled",
        "disaster": "Disaster Impact Noted",
        "contact_made": "Contact Made",
    }
    
    @classmethod
    def build_memo_from_context(
        cls,
        context: Dict[str, Any],
        conversation_history: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Build memo from Node Engine context.
        
        Args:
            context: Final context from Node Engine with all collected data
            conversation_history: List of conversation messages
            
        Returns:
            Memo dictionary ready for API posting
        """
        # Determine service type from context
        service_type = cls._determine_service_type(context)
        logger.info(f"[MEMO] Building memo for service type: {service_type}")
        
        # Get recording/conversation ID
        conversation_id = (
            context.get("recording_sid") or 
            context.get("call_sid") or
            ""
        )
        
        # Build base memo
        memo = {
            # Required fields
            "Loan_ID": context.get("LoanID"),
            "Subject": cls.SUBJECT_MAP.get(service_type, "Customer Contact - Outbound Collections"),
            "Date_Time": datetime.now(timezone.utc).isoformat(),
            "Category": "Collections",
            "User": "FICSAPI",
            "Notify_on_Date": cls._calculate_notify_date(context, service_type),
            "Code": "Collections",
            "ConversationID": conversation_id,
            
            # Contact info
            "WhoYouSpokeTo": cls._get_contact_name(context),
            "Disposition": cls.DISPOSITION_MAP.get(service_type, "Contact Made"),
            "Call Status": "Completed",
            "Direction": "Outbound",
        }
        
        # Add service-specific fields
        memo.update(cls._get_service_specific_fields(context, service_type))
        
        # Add optional fields
        memo.update({
            "Occupancy": context.get("occupancy_status"),
            "ReasonForDlqTimeline": context.get("delinquency_reason"),
            "DQResolutionplanned": context.get("resolution_plan"),
            "AlternatePaymentOptionsDiscussed": "Yes" if context.get("borrower_wants_options") else None,
            "OtherInformation": cls._build_other_info(context, service_type),
            "BorrowerID": context.get("BorrowerID"),
        })
        
        # Add flags based on service type
        memo.update(cls._get_flags(service_type, context))
        
        # Generate AI summary
        try:
            from app.utils.summary_generator import generate_summary
            memo["CallSummary"] = generate_summary(conversation_history, memo)
        except Exception as e:
            logger.error(f"Failed to generate AI summary: {e}")
            memo["CallSummary"] = cls._generate_fallback_summary(context, service_type)
        
        # Ensure all optional fields have None if not set
        cls._normalize_optional_fields(memo)
        
        logger.info(f"[MEMO] Built memo with {len([k for k, v in memo.items() if v is not None])} populated fields")
        
        return memo
    
    @classmethod
    def _determine_service_type(cls, context: Dict[str, Any]) -> str:
        """
        Determine the service type from context variables.
        
        Priority order:
        1. Transfer completed
        2. Callback scheduled
        3. Appointment scheduled
        4. User claims payment made
        5. Payment processed (now vs scheduled)
        6. Promise to pay (declined setup)
        7. Disaster impact
        8. Default: contact_made
        """
        # Transfer
        if context.get("transfer_completed") or context.get("transfer_requested"):
            return "transfer"
        
        # Callback
        if context.get("callback_scheduled") or context.get("callback_time_confirmed"):
            return "callback"
        
        # Appointment
        if context.get("appt_scheduled_success") or context.get("appointment_confirmed"):
            return "appointment"
        
        # Payment already made
        if context.get("user_claims_payment_made"):
            return "payment_already_made"
        
        # Payment processed
        if context.get("payment_processed") or context.get("confirmation_number"):
            payment_date = context.get("upd_extracted_payment_date")
            current_date = context.get("current_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            
            if payment_date == current_date:
                return "payment_now"
            return "schedule_payment"
        
        # Promise to pay
        if context.get("declined_bank_account_setup_today") or context.get("user_wants_set_up_later"):
            return "promise_to_pay"
        
        # Disaster
        if context.get("affected_by_disaster"):
            return "disaster"
        
        return "contact_made"
    
    @classmethod
    def _get_service_specific_fields(cls, context: Dict[str, Any], service_type: str) -> Dict[str, Any]:
        """Get fields specific to each service type."""
        fields = {}
        
        if service_type in ["payment_now", "schedule_payment"]:
            fields["PaymentAmount"] = context.get("user_provided_payment_amount")
            fields["PaymentDate"] = context.get("upd_extracted_payment_date")
            fields["Method"] = cls._get_payment_method(context)
            fields["Confirmation"] = context.get("confirmation_number")
            
        elif service_type == "promise_to_pay":
            fields["PromiseToPay"] = cls._build_promise_statement(context)
            fields["PaymentAmount"] = context.get("user_provided_payment_amount")
            fields["PaymentDate"] = context.get("upd_extracted_payment_date")
            fields["Method"] = context.get("alternative_method")
            
        elif service_type == "payment_already_made":
            fields["PaymentAmount"] = context.get("claimed_payment_amount")
            fields["PaymentDate"] = context.get("claimed_payment_date")
            fields["Method"] = context.get("claimed_payment_method")
            
        elif service_type == "callback":
            fields["CallbackTime"] = context.get("callback_time")
            fields["AfterhoursCallbackAgreement"] = context.get("afterhours_callback")
            
        elif service_type == "appointment":
            fields["OtherInformation"] = f"Appointment scheduled for {context.get('appointment_datetime', 'TBD')}"
            
        return fields
    
    @classmethod
    def _get_payment_method(cls, context: Dict[str, Any]) -> Optional[str]:
        """Determine payment method from context."""
        if context.get("existing_bank_account_confirmed"):
            return "C"  # Existing checking
        if context.get("new_bank_account_confirmed"):
            if context.get("new_account_payment_method") == "savings":
                return "S"  # Savings
            return "N"  # New checking
        if context.get("certified_funds_mail_date_confirmed"):
            return "CF"  # Certified funds
        return context.get("payment_method")
    
    @classmethod
    def _build_promise_statement(cls, context: Dict[str, Any]) -> Optional[str]:
        """Build promise to pay statement."""
        amount = context.get("user_provided_payment_amount")
        date = context.get("upd_extracted_payment_date")
        method = context.get("alternative_method", "online")
        
        if amount and date:
            return f"Customer promised ${amount} by {date} via {method}"
        elif amount:
            return f"Customer promised ${amount}"
        return "Customer made promise to pay"
    
    @classmethod
    def _get_contact_name(cls, context: Dict[str, Any]) -> str:
        """Get the name of who we spoke to."""
        first_name = context.get("FirstName", "")
        last_name = context.get("LastName", "")
        
        if first_name or last_name:
            return f"{first_name} {last_name}".strip()
        
        # Check if we spoke to someone else
        if context.get("party_name"):
            return context.get("party_name")
        
        return "Borrower"
    
    @classmethod
    def _get_flags(cls, service_type: str, context: Dict[str, Any]) -> Dict[str, Optional[int]]:
        """Get flag values based on service type."""
        flags = {
            "Payment": None,
            "Promise to Pay": None,
            "Callback": None,
            "Transferred": None,
            "Transferred Connected Calls": None,
            "No Connection": None,
            "Missing Conversation ID": None,
            "Missing Loan ID": None,
        }
        
        if service_type in ["payment_now", "schedule_payment"]:
            flags["Payment"] = 1
            
        elif service_type == "promise_to_pay":
            flags["Promise to Pay"] = 1
            
        elif service_type == "callback":
            flags["Callback"] = 1
            
        elif service_type == "transfer":
            flags["Transferred"] = 1
            flags["Transferred Connected Calls"] = 1
        
        # Check for missing data
        if not context.get("recording_sid") and not context.get("call_sid"):
            flags["Missing Conversation ID"] = 1
        
        if not context.get("LoanID"):
            flags["Missing Loan ID"] = 1
        
        return flags
    
    @classmethod
    def _calculate_notify_date(cls, context: Dict[str, Any], service_type: str) -> str:
        """Calculate notification date."""
        # For scheduled payments, notify day before
        if service_type == "schedule_payment":
            payment_date = context.get("upd_extracted_payment_date")
            if payment_date:
                try:
                    dt = datetime.strptime(payment_date, "%Y-%m-%d")
                    notify_dt = dt - timedelta(days=1)
                    return notify_dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        
        # For callbacks, notify on callback date
        if service_type == "callback":
            callback_time = context.get("callback_time")
            if callback_time:
                try:
                    dt = datetime.fromisoformat(callback_time.replace('Z', '+00:00'))
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        
        # For appointments, notify on appointment date
        if service_type == "appointment":
            appt_date = context.get("appointment_datetime")
            if appt_date:
                try:
                    dt = datetime.fromisoformat(appt_date.replace('Z', '+00:00'))
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        
        # Default: today (UTC)
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    @classmethod
    def _build_other_info(cls, context: Dict[str, Any], service_type: str) -> Optional[str]:
        """Build other information field."""
        info_parts = []
        
        # Payment already made details
        if service_type == "payment_already_made":
            method = context.get("claimed_payment_method", "")
            date = context.get("claimed_payment_date", "")
            info_parts.append(f"Customer claims payment sent {date} via {method}")
        
        # Disaster impact
        if context.get("affected_by_disaster"):
            info_parts.append("Customer affected by disaster - referred to loss mitigation")
        
        # DOB verification attempts
        dob_attempts = context.get("dob_attempts", 0)
        if dob_attempts > 1:
            info_parts.append(f"DOB verification required {dob_attempts} attempts")
        
        # Transfer reason
        if context.get("transfer_reason"):
            info_parts.append(f"Transfer reason: {context.get('transfer_reason')}")
        
        return "; ".join(info_parts) if info_parts else None
    
    @classmethod
    def _generate_fallback_summary(cls, context: Dict[str, Any], service_type: str) -> str:
        """Generate fallback summary if AI generation fails."""
        customer_name = cls._get_contact_name(context)
        amount = context.get("user_provided_payment_amount") or context.get("TotalAmountDue", "")
        
        summaries = {
            "payment_now": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Payment of ${amount} was successfully processed. Call completed successfully.",
            "schedule_payment": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Payment of ${amount} was scheduled for {context.get('upd_extracted_payment_date', 'a future date')}. Call completed successfully.",
            "promise_to_pay": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Customer made a promise to pay commitment. Call completed successfully.",
            "payment_already_made": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Customer indicated payment has already been made. Call completed successfully.",
            "callback": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Callback was scheduled at customer's request. Call completed successfully.",
            "transfer": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Call was transferred to Level 2 agent for further assistance.",
            "appointment": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Appointment was scheduled with loss mitigation team. Call completed successfully.",
            "disaster": f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Customer reported being affected by a disaster and was referred to loss mitigation. Call completed successfully.",
        }
        
        return summaries.get(
            service_type, 
            f"Customer {customer_name} was contacted regarding their delinquent mortgage account. Agent discussed payment options with customer. Call completed successfully."
        )
    
    @classmethod
    def _normalize_optional_fields(cls, memo: Dict[str, Any]) -> None:
        """Ensure all optional fields exist with None if not set."""
        optional_fields = [
            "GaveTotAmtDue", "PaymentAmount", "PaymentDate", "Method", "Confirmation",
            "PromiseToPay", "CallbackTime", "AfterhoursCallbackAgreement",
            "Occupancy", "ReasonForDlqTimeline", "DQResolutionplanned",
            "AlternatePaymentOptionsDiscussed", "IfTP-DocumentRelationship",
            "OtherInformation", "BorrowerID", "Payment", "Promise to Pay", "Callback",
            "Transferred", "Transferred Connected Calls", "Missing Conversation ID",
            "No Connection", "Avery Connected", "Missing Loan ID", "RecordingProcessed",
        ]
        
        for field in optional_fields:
            if field not in memo:
                memo[field] = None
    
    # ==========================================================================
    # LEGACY SUPPORT - Keep for backwards compatibility
    # ==========================================================================
    
    @staticmethod
    def extract_all_variables(
        call_context: Dict[str, Any],
        conversation_history: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Legacy method for backwards compatibility.
        Converts old call_context format to new context format.
        """
        # If this is already Node Engine context, use it directly
        if "LoanID" in call_context or "current_node" in call_context:
            return MemoBuilder.build_memo_from_context(call_context, conversation_history)
        
        # Convert old format to new format
        customer_data = call_context.get("customer_data", {})
        collected_data = call_context.get("collected_data", {})
        
        # Merge completed services data
        completed_services = call_context.get("completed_services", [])
        for service_data in completed_services:
            collected_data.update(service_data.get("data", {}))
        
        # Build context in new format
        context = {
            # Customer data
            "LoanID": customer_data.get("loan_number") or customer_data.get("loan_id") or customer_data.get("Loan_ID"),
            "FirstName": customer_data.get("FirstName") or customer_data.get("name", "").split()[0] if customer_data.get("name") else "",
            "LastName": customer_data.get("LastName") or (" ".join(customer_data.get("name", "").split()[1:]) if customer_data.get("name") else ""),
            "TotalAmountDue": customer_data.get("TotalAmountDue") or customer_data.get("total_amount_due"),
            
            # Call metadata
            "call_sid": call_context.get("call_sid") or call_context.get("stream_sid"),
            "recording_sid": call_context.get("recording_sid"),
            
            # Collected data mapping
            "user_provided_payment_amount": collected_data.get("payment_amount") or collected_data.get("amount"),
            "upd_extracted_payment_date": collected_data.get("payment_date") or collected_data.get("scheduled_date"),
            "payment_method": collected_data.get("method") or collected_data.get("payment_method"),
            "confirmation_number": collected_data.get("confirmation") or collected_data.get("confirmation_number"),
            "occupancy_status": collected_data.get("occupancy_status") or collected_data.get("occupancy"),
            "delinquency_reason": collected_data.get("delinquency_reason") or collected_data.get("default_reason"),
            "callback_time": collected_data.get("callback_time") or collected_data.get("scheduled_time"),
            
            # Infer booleans from service type
            "payment_processed": call_context.get("current_service") in ["payment_now", "schedule_payment"] and call_context.get("service_state") == "completed",
            "transfer_completed": call_context.get("current_service") == "transfer",
            "callback_scheduled": call_context.get("current_service") == "callback_scheduling",
            "user_claims_payment_made": call_context.get("current_service") == "payment_already_made",
            "declined_bank_account_setup_today": call_context.get("current_service") == "promise_to_pay",
        }
        
        return MemoBuilder.build_memo_from_context(context, conversation_history)