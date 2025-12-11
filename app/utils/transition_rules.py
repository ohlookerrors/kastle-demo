"""
Transition Rules - Determine next node based on current node and extracted variables.

All routing logic is centralized here. No LLM needed for transitions - 
just simple if/else based on extracted variables.
"""

import logging
from typing import Dict, Any, List, Tuple, Callable, Optional

logger = logging.getLogger(__name__)


# Type alias for transition rules
# (condition_function, target_node, description)
TransitionRule = Tuple[Callable[[Dict, Dict], bool], str, str]


# =============================================================================
# GLOBAL TRIGGERS - Checked for ALL nodes before node-specific rules
# =============================================================================

GLOBAL_TRIGGERS: List[TransitionRule] = [
    # Transfer requests
    (lambda v, c: v.get("user_requests_live_agent"), "n34", "User requests live agent"),
    (lambda v, c: v.get("user_requests_supervisor"), "n34", "User requests supervisor"),
    (lambda v, c: v.get("user_requests_transfer"), "n34", "User requests transfer"),
    
    # Legal/Compliance triggers
    (lambda v, c: v.get("user_mentions_attorney"), "n5", "Attorney notification"),
    (lambda v, c: v.get("user_represented_by_attorney"), "n5", "Represented by attorney"),
    (lambda v, c: v.get("user_requests_cease_communication"), "n11", "Cease and desist"),
    (lambda v, c: v.get("user_requests_written_only"), "n11", "Written communication only"),
    
    # Wrong number
    (lambda v, c: v.get("user_says_wrong_number"), "n69", "Wrong number"),
    (lambda v, c: v.get("wrong_person"), "n69", "Wrong person"),
    
    # Complex questions requiring L2
    (lambda v, c: v.get("user_has_complex_question"), "n34", "Complex question - transfer"),
    (lambda v, c: v.get("user_asks_about_nsf"), "n34", "NSF question - transfer"),
    (lambda v, c: v.get("user_asks_about_escrow"), "n34", "Escrow question - transfer"),
]


# =============================================================================
# NODE-SPECIFIC TRANSITION RULES
# =============================================================================

TRANSITION_RULES: Dict[str, List[TransitionRule]] = {
    
    # -------------------------------------------------------------------------
    # GREETING & IDENTITY (n61)
    # -------------------------------------------------------------------------
    "n61": [
        (lambda v, c: v.get("is_borrower") or v.get("confirmed_identity"), "n68", "Identity confirmed - go to DOB"),
        (lambda v, c: v.get("party_name"), "n68", "Got party name - go to DOB"),
        (lambda v, c: v.get("speaking_to_borrower"), "n68", "Speaking to borrower - go to DOB"),
        (lambda v, c: v.get("user_not_available"), "n8", "Borrower not available - offer callback"),
        (lambda v, c: v.get("call_back_later"), "n8", "Call back later requested"),
        # Default: stay and wait for identity confirmation
    ],
    
    # -------------------------------------------------------------------------
    # DOB VERIFICATION - First Attempt (n68)
    # -------------------------------------------------------------------------
    "n68": [
        (lambda v, c: v.get("dob_verified"), "n41", "DOB correct - go to Mini Miranda"),
        (lambda v, c: v.get("dob_correct"), "n41", "DOB matches - go to Mini Miranda"),
        (lambda v, c: v.get("dob_mismatch"), "n32", "DOB wrong - notify mismatch"),
        (lambda v, c: v.get("dob_incorrect"), "n32", "DOB incorrect - notify mismatch"),
        (lambda v, c: c.get("dob_attempts", 0) >= 5, "n34", "Too many DOB attempts - transfer"),
        # Default: stay and wait for DOB
    ],
    
    # -------------------------------------------------------------------------
    # DOB MISMATCH NOTIFICATION (n32)
    # -------------------------------------------------------------------------
    "n32": [
        (lambda v, c: True, "n22", "Always go to second DOB attempt"),
    ],
    
    # -------------------------------------------------------------------------
    # DOB VERIFICATION - Second Attempt (n22)
    # -------------------------------------------------------------------------
    "n22": [
        (lambda v, c: v.get("dob_verified") or v.get("dob_reconfirmed"), "n41", "DOB correct - go to Mini Miranda"),
        (lambda v, c: v.get("dob_correct"), "n41", "DOB matches - go to Mini Miranda"),
        (lambda v, c: c.get("dob_attempts", 0) >= 5, "n34", "Too many attempts - transfer"),
        (lambda v, c: v.get("dob_still_wrong") or v.get("dob_mismatch"), "n26", "Still wrong - end call"),
        # Default: stay and wait
    ],
    
    # -------------------------------------------------------------------------
    # DOB FAILED - END CALL (n26)
    # -------------------------------------------------------------------------
    "n26": [
        (lambda v, c: True, "END", "DOB verification failed - end call"),
    ],
    
    # -------------------------------------------------------------------------
    # MINI MIRANDA (n41)
    # -------------------------------------------------------------------------
    "n41": [
        (lambda v, c: v.get("mini_miranda_complete"), "n45", "Disclosure complete - check occupancy"),
        (lambda v, c: v.get("user_acknowledges"), "n45", "User acknowledged - check occupancy"),
        (lambda v, c: v.get("proceed_to_business"), "n45", "Ready to proceed - check occupancy"),
        # Default: stay and complete disclosure
    ],
    
    # -------------------------------------------------------------------------
    # OCCUPANCY VERIFICATION (n45)
    # -------------------------------------------------------------------------
    "n45": [
        # Check for valid occupancy value (O-OCC, V-Vac, T-3rd) - not N/A or empty
        (lambda v, c: v.get("occupancy") and v.get("occupancy") not in ["N/A", "null", None, ""], "n20", "Occupancy verified - check disaster"),
        # Fallback checks for compatibility
        (lambda v, c: v.get("occupancy_verified"), "n20", "Occupancy verified flag - check disaster"),
        (lambda v, c: v.get("occupancy_confirmed"), "n20", "Occupancy confirmed flag - check disaster"),
        (lambda v, c: v.get("occupancy_status"), "n20", "Got occupancy status - check disaster"),
        # Default: stay and verify occupancy
    ],
    
    # -------------------------------------------------------------------------
    # DISASTER CHECK (n20)
    # -------------------------------------------------------------------------
    "n20": [
        (lambda v, c: v.get("affected_by_disaster") == True, "n37", "Disaster affected - loss mitigation"),
        (lambda v, c: v.get("disaster_impact") == True, "n37", "Disaster impact - loss mitigation"),
        (lambda v, c: v.get("affected_by_disaster") == False, "n28", "Not affected - continue to payment"),
        (lambda v, c: v.get("not_affected_by_disaster"), "n28", "Not affected - continue to payment"),
        (lambda v, c: v.get("no_disaster_impact"), "n28", "No impact - continue to payment"),
        # Default: stay and check
    ],
    
    # -------------------------------------------------------------------------
    # NOT AFFECTED / CONTINUE (n28)
    # -------------------------------------------------------------------------
    "n28": [
        (lambda v, c: True, "n49", "Continue to payment collection"),
    ],
    
    # -------------------------------------------------------------------------
    # LOSS MITIGATION (n37)
    # -------------------------------------------------------------------------
    "n37": [
        (lambda v, c: v.get("wants_appointment"), "n56", "User wants appointment"),
        (lambda v, c: v.get("schedule_appointment"), "n56", "Schedule appointment requested"),
        (lambda v, c: v.get("wants_callback"), "n8", "User wants callback"),
        (lambda v, c: v.get("user_wants_to_end_call"), "n25", "User wants to end call"),
        # Default: stay and discuss options
    ],
    
    # -------------------------------------------------------------------------
    # PAYMENT COLLECTION (n49) - MAIN HUB
    # -------------------------------------------------------------------------
    "n49": [
        # Already paid scenarios
        (lambda v, c: v.get("user_claims_payment_made"), "n51", "User claims already paid - confirmation"),
        (lambda v, c: v.get("payment_already_sent"), "n51", "Payment already sent - confirmation"),

        # Promise to pay / Set up later
        (lambda v, c: v.get("user_wants_set_up_later"), "n51", "User wants to set up later - promise"),
        (lambda v, c: v.get("declined_bank_account_setup_today"), "n51", "Declined setup - promise"),
        (lambda v, c: v.get("will_pay_independently"), "n51", "Will pay independently - promise"),

        # Got payment info - proceed to validation
        (lambda v, c: v.get("payment_date_received") and v.get("payment_amount_received"), "n67", "Got date and amount - validate"),
        (lambda v, c: v.get("user_provided_payment_amount") and v.get("upd_extracted_payment_date"), "n67", "Have amount and date - validate"),
        # If user confirmed amount and waterfall complete, proceed to validation (date may be implied as "today")
        (lambda v, c: v.get("payment_amount_received") and v.get("collection_waterfall_completed") and v.get("total_amount_due_informed"), "n67", "Amount confirmed - proceed to validation"),

        # User wants options - MUST have been asked first (options_question_asked) to prevent false triggers
        (lambda v, c: v.get("borrower_wants_options") and v.get("options_question_asked"), "n23", "User wants payment options"),
        (lambda v, c: v.get("borrower_requests_options_directly"), "n23", "User directly requests assistance programs"),
        (lambda v, c: v.get("needs_assistance") and v.get("options_question_asked"), "n23", "User needs assistance - show options"),
        (lambda v, c: v.get("financial_hardship") and v.get("options_question_asked"), "n23", "Financial hardship - show options"),

        # Delinquency reason capture
        (lambda v, c: v.get("capture_delinquency_reason"), "n19", "Capture delinquency reason"),

        # Default: stay and collect payment info
    ],
    
    # -------------------------------------------------------------------------
    # DELINQUENCY REASON (n19)
    # -------------------------------------------------------------------------
    "n19": [
        (lambda v, c: v.get("reason_captured") or v.get("delinquency_reason"), "n49", "Reason captured - back to payment"),
        # Default: stay and capture reason
    ],
    
    # -------------------------------------------------------------------------
    # PAYMENT VALIDATION (n67)
    # -------------------------------------------------------------------------
    "n67": [
        # User wants to hear about options - go to payment options
        (lambda v, c: v.get("borrower_requests_options_directly"), "n23", "User asks about options - show options"),

        # Check if user provided both amount and date (actual extracted vars from config)
        # IMPORTANT: Check BOTH amount AND date are not NA/empty
        (lambda v, c: (
            v.get("user_provided_payment_amount") and
            v.get("user_provided_payment_amount") not in ["NA", "N/A", None, ""] and
            v.get("user_provided_payment_date") and
            v.get("user_provided_payment_date") not in ["NA", "N/A", None, ""]
        ), "n1", "Payment details confirmed - collect account"),

        # Fallback checks for compatibility
        (lambda v, c: v.get("validation_confirmed"), "n1", "Validated - collect account"),
        (lambda v, c: v.get("user_confirms_amount"), "n1", "Amount confirmed - collect account"),
        (lambda v, c: v.get("details_confirmed"), "n1", "Details confirmed - collect account"),
        (lambda v, c: v.get("user_wants_to_change_amount"), "n49", "Change amount - back to collection"),
        (lambda v, c: v.get("user_wants_to_change_date"), "n49", "Change date - back to collection"),
        # Default: stay and confirm
    ],
    
    # -------------------------------------------------------------------------
    # ACCOUNT COLLECTION (n1)
    # -------------------------------------------------------------------------
    "n1": [
        # User declines to provide account
        (lambda v, c: v.get("declined_bank_account_setup_today"), "n51", "Declined - promise to pay"),
        (lambda v, c: v.get("user_wants_set_up_later"), "n51", "Set up later - promise to pay"),
        (lambda v, c: v.get("will_pay_online"), "n51", "Will pay online - promise"),
        (lambda v, c: v.get("will_mail_check"), "n51", "Will mail check - promise"),
        
        # Account confirmed - proceed to NACHA
        (lambda v, c: v.get("existing_bank_account_confirmed"), "n42", "Existing account confirmed - NACHA"),
        (lambda v, c: v.get("new_bank_account_confirmed"), "n42", "New account confirmed - NACHA"),
        (lambda v, c: v.get("account_ready"), "n42", "Account ready - NACHA"),
        
        # Certified funds path
        (lambda v, c: v.get("certified_funds_mail_date_confirmed"), "n12", "Certified funds confirmed"),
        (lambda v, c: c.get("RestrictAutoPayDraft") == "Y" and v.get("mail_date_confirmed"), "n12", "Certified funds date confirmed"),
        
        # Default: stay and collect account
    ],
    
    # -------------------------------------------------------------------------
    # NACHA DISCLOSURE (n42)
    # -------------------------------------------------------------------------
    "n42": [
        # User declines
        (lambda v, c: v.get("user_says_no"), "n49", "User declined - back to collection"),
        (lambda v, c: v.get("user_declines_authorization"), "n49", "Declined auth - back to collection"),
        
        # User wants to change
        (lambda v, c: v.get("user_wants_to_change_amtdate"), "n49", "Change requested - back to collection"),
        (lambda v, c: v.get("user_wants_different_amount"), "n49", "Different amount - back to collection"),
        
        # Permission granted - process payment
        (lambda v, c: v.get("nacha_permission_granted"), "n50", "Permission granted - process"),
        (lambda v, c: v.get("user_authorizes_payment"), "n50", "Authorized - process"),
        (lambda v, c: v.get("user_confirms_authorization"), "n50", "Confirmed auth - process"),
        
        # Default: stay and get authorization
    ],
    
    # -------------------------------------------------------------------------
    # PAYMENT PROCESSING (n50)
    # -------------------------------------------------------------------------
    "n50": [
        # Success
        (lambda v, c: v.get("payment_processed"), "n51", "Payment processed - confirmation"),
        (lambda v, c: c.get("api_status_code") == 200, "n51", "API success - confirmation"),
        (lambda v, c: c.get("confirmation_number"), "n51", "Got confirmation - success"),
        
        # Failure - transfer to L2
        (lambda v, c: c.get("api_status_code") and c.get("api_status_code") != 200, "n34", "API failed - transfer"),
        (lambda v, c: c.get("api_error"), "n34", "API error - transfer"),
        (lambda v, c: v.get("payment_failed"), "n34", "Payment failed - transfer"),
        
        # Default: stay (processing)
    ],
    
    # -------------------------------------------------------------------------
    # CONFIRMATION / PROMISE TO PAY (n51)
    # -------------------------------------------------------------------------
    "n51": [
        (lambda v, c: v.get("call_complete"), "n25", "Call complete - end"),
        (lambda v, c: v.get("no_more_questions"), "n25", "No more questions - end"),
        (lambda v, c: v.get("user_satisfied"), "n25", "User satisfied - end"),
        (lambda v, c: v.get("goodbye_said"), "n25", "Goodbye - end"),
        # Default: stay and handle questions
    ],
    
    # -------------------------------------------------------------------------
    # PAYMENT OPTIONS (n23)
    # -------------------------------------------------------------------------
    "n23": [
        # User has no more questions about options - go back to payment collection
        (lambda v, c: v.get("user_has_no_other_questions"), "n49", "No more questions - back to payment"),

        # Legacy/compatibility rules
        (lambda v, c: v.get("option_selected"), "n49", "Option selected - back to payment"),
        (lambda v, c: v.get("ready_to_pay"), "n49", "Ready to pay - back to payment"),
        (lambda v, c: v.get("wants_appointment"), "n56", "Wants appointment - schedule"),
        (lambda v, c: v.get("schedule_appointment"), "n56", "Schedule appointment"),
        (lambda v, c: v.get("wants_callback"), "n8", "Wants callback"),
        (lambda v, c: v.get("needs_more_time"), "n8", "Needs more time - offer callback"),
        # Default: stay and discuss options
    ],
    
    # -------------------------------------------------------------------------
    # CERTIFIED FUNDS CONFIRMATION (n12)
    # -------------------------------------------------------------------------
    "n12": [
        (lambda v, c: v.get("user_has_no_other_questions"), "n25", "No questions - end call"),
        (lambda v, c: v.get("call_complete"), "n25", "Complete - end call"),
        # Default: stay and handle questions
    ],
    
    # -------------------------------------------------------------------------
    # TRANSFER INTAKE (n34)
    # -------------------------------------------------------------------------
    "n34": [
        (lambda v, c: v.get("transfer_intake_complete"), "n35", "Intake complete - confirm transfer"),
        (lambda v, c: v.get("transfer_reason") and v.get("ready_to_transfer"), "n35", "Ready to transfer"),
        # Default: stay and collect transfer reason
    ],
    
    # -------------------------------------------------------------------------
    # TRANSFER CONFIRMATION (n35)
    # -------------------------------------------------------------------------
    "n35": [
        (lambda v, c: v.get("user_confirms_transfer"), "n36", "Transfer confirmed - execute"),
        (lambda v, c: v.get("proceed_with_transfer"), "n36", "Proceed - execute transfer"),
        (lambda v, c: v.get("user_cancels_transfer"), "n49", "Cancelled - back to payment"),
        # Default: stay and confirm
    ],
    
    # -------------------------------------------------------------------------
    # EXECUTE TRANSFER (n36)
    # -------------------------------------------------------------------------
    "n36": [
        (lambda v, c: v.get("transfer_completed"), "n2", "Transfer done - end"),
        (lambda v, c: c.get("transfer_completed"), "n2", "Transfer executed - end"),
        # Default: execute transfer
    ],
    
    # -------------------------------------------------------------------------
    # ATTORNEY NOTIFICATION (n5)
    # -------------------------------------------------------------------------
    "n5": [
        (lambda v, c: v.get("attorney_noted"), "n25", "Attorney noted - end call"),
        (lambda v, c: True, "n25", "End call after attorney notification"),
    ],
    
    # -------------------------------------------------------------------------
    # CEASE & DESIST (n11)
    # -------------------------------------------------------------------------
    "n11": [
        (lambda v, c: True, "n25", "Cease communication - end call"),
    ],
    
    # -------------------------------------------------------------------------
    # CALLBACK OFFERING (n8)
    # -------------------------------------------------------------------------
    "n8": [
        (lambda v, c: v.get("callback_time_confirmed"), "n9", "Callback time confirmed"),
        (lambda v, c: v.get("callback_scheduled"), "n9", "Callback scheduled"),
        (lambda v, c: v.get("user_declines_callback"), "n25", "Declined callback - end"),
        (lambda v, c: v.get("no_callback_needed"), "n25", "No callback - end"),
        # Default: stay and get callback time
    ],
    
    # -------------------------------------------------------------------------
    # CALLBACK CONFIRMED (n9)
    # -------------------------------------------------------------------------
    "n9": [
        (lambda v, c: True, "n25", "Callback confirmed - end call"),
    ],
    
    # -------------------------------------------------------------------------
    # APPOINTMENT SCHEDULING (n56)
    # -------------------------------------------------------------------------
    "n56": [
        (lambda v, c: v.get("user_time_preference"), "n6", "Got preference - fetch slots"),
        (lambda v, c: v.get("preferred_day"), "n6", "Got preferred day - fetch slots"),
        (lambda v, c: v.get("preferred_time"), "n6", "Got preferred time - fetch slots"),
        # Default: stay and get preference
    ],
    
    # -------------------------------------------------------------------------
    # GET AVAILABLE SLOTS (n6 - API)
    # -------------------------------------------------------------------------
    "n6": [
        (lambda v, c: c.get("api_status_code") == 200, "n4", "Slots received - offer times"),
        (lambda v, c: v.get("slots_available"), "n4", "Slots available - offer times"),
        (lambda v, c: c.get("api_error"), "n34", "API error - transfer"),
        # Default: waiting for API
    ],
    
    # -------------------------------------------------------------------------
    # OFFER TIME SLOTS (n4)
    # -------------------------------------------------------------------------
    "n4": [
        (lambda v, c: v.get("specific_time_selected"), "n3", "Time selected - confirm"),
        (lambda v, c: v.get("user_selected_slot"), "n3", "Slot selected - confirm"),
        (lambda v, c: v.get("user_appt_conflict"), "n56", "Conflict - get new preference"),
        (lambda v, c: v.get("none_work"), "n56", "None work - get new preference"),
        # Default: stay and let user select
    ],
    
    # -------------------------------------------------------------------------
    # CONFIRM APPOINTMENT (n3)
    # -------------------------------------------------------------------------
    "n3": [
        (lambda v, c: v.get("appointment_confirmed"), "n62", "Appointment booked - success"),
        (lambda v, c: v.get("appt_booked"), "n62", "Booked - success"),
        (lambda v, c: v.get("user_cancels"), "n56", "Cancelled - back to scheduling"),
        # Default: stay and confirm
    ],
    
    # -------------------------------------------------------------------------
    # APPOINTMENT SUCCESS (n62)
    # -------------------------------------------------------------------------
    "n62": [
        (lambda v, c: True, "n25", "Appointment done - end call"),
    ],
    
    # -------------------------------------------------------------------------
    # WRONG NUMBER (n69)
    # -------------------------------------------------------------------------
    "n69": [
        (lambda v, c: True, "END", "Wrong number - end call"),
    ],
    
    # -------------------------------------------------------------------------
    # CALL ENDINGS
    # -------------------------------------------------------------------------
    "n25": [(lambda v, c: True, "END", "Standard call ending")],
    "n24": [(lambda v, c: True, "END", "Alternative call ending")],
    "n2": [(lambda v, c: True, "END", "Transfer completed ending")],
}


def get_next_node(
    current_node: str, 
    extracted_vars: Dict[str, Any], 
    context: Dict[str, Any]
) -> str:
    """
    Determine the next node based on current node and extracted variables.
    
    Args:
        current_node: Current node ID (e.g., 'n49')
        extracted_vars: Variables extracted from current conversation turn
        context: Full context dictionary
        
    Returns:
        Next node ID or 'END' if call should end
    """
    # Check global triggers first (these override node-specific rules)
    for condition, target, description in GLOBAL_TRIGGERS:
        try:
            if condition(extracted_vars, context):
                logger.info(f"Global trigger: {description} -> {target}")
                return target
        except Exception as e:
            logger.error(f"Error checking global trigger '{description}': {e}")
            continue
    
    # Get node-specific rules
    rules = TRANSITION_RULES.get(current_node, [])

    logger.info(f"🔀 [TRANSITION] Checking rules for node: {current_node}")
    logger.info(f"🔀 [TRANSITION] Extracted vars: {extracted_vars}")

    if not rules:
        logger.warning(f"⚠️ No transition rules found for node: {current_node}")
        return current_node  # Stay in current node

    # Check each rule in order
    for condition, target, description in rules:
        try:
            result = condition(extracted_vars, context)
            logger.debug(f"   Rule '{description}' -> {target}: {result}")
            if result:
                logger.info(f"✅ [TRANSITION MATCH] {current_node} -> {target} ({description})")
                return target
        except Exception as e:
            logger.error(f"❌ Error checking rule '{description}' for {current_node}: {e}")
            continue

    # Default: stay in current node
    logger.info(f"⏸️ [NO MATCH] Staying at node: {current_node}")
    return current_node


def get_node_description(node_id: str) -> str:
    """
    Get a human-readable description of what a node does.
    
    Args:
        node_id: Node identifier
        
    Returns:
        Description string
    """
    descriptions = {
        "n61": "Greeting & Identity Confirmation",
        "n68": "DOB Verification (Attempt 1)",
        "n32": "DOB Mismatch Notification",
        "n22": "DOB Verification (Attempt 2)",
        "n26": "DOB Failed - End Call",
        "n41": "Mini Miranda Disclosure",
        "n45": "Occupancy Verification",
        "n20": "Disaster Impact Check",
        "n28": "Continue to Payment",
        "n37": "Loss Mitigation Discussion",
        "n49": "Payment Collection",
        "n19": "Delinquency Reason Capture",
        "n67": "Payment Validation",
        "n1": "Account Collection",
        "n42": "NACHA Authorization",
        "n50": "Payment Processing",
        "n51": "Confirmation / Promise to Pay",
        "n23": "Payment Options",
        "n12": "Certified Funds Confirmation",
        "n34": "Transfer Intake",
        "n35": "Transfer Confirmation",
        "n36": "Execute Transfer",
        "n5": "Attorney Notification",
        "n11": "Cease & Desist",
        "n8": "Callback Offering",
        "n9": "Callback Confirmed",
        "n56": "Appointment Scheduling",
        "n6": "Fetch Available Slots",
        "n4": "Offer Time Slots",
        "n3": "Confirm Appointment",
        "n62": "Appointment Success",
        "n69": "Wrong Number",
        "n25": "Call Ending (Standard)",
        "n24": "Call Ending (Alternative)",
        "n2": "Call Ending (Transfer Complete)",
        "END": "Call Ended",
    }
    return descriptions.get(node_id, f"Unknown Node ({node_id})")


def get_all_target_nodes(node_id: str) -> List[str]:
    """
    Get all possible target nodes from a given node.
    Useful for visualization and testing.
    
    Args:
        node_id: Source node ID
        
    Returns:
        List of possible target node IDs
    """
    targets = set()
    
    # Add global trigger targets
    for _, target, _ in GLOBAL_TRIGGERS:
        targets.add(target)
    
    # Add node-specific targets
    rules = TRANSITION_RULES.get(node_id, [])
    for _, target, _ in rules:
        targets.add(target)
    
    return list(targets)