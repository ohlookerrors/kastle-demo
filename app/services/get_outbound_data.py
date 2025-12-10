import httpx
from app.config import logger
from typing import Optional, Dict
import os
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
API_USER = os.getenv("API_USER")

# =============================================================================
# DUMMY DATA FOR TESTING
# To disable: Set USE_DUMMY_DATA=false in .env file
# To remove: Delete this section and the if blocks in fetch_caller_data()
#            and fetch_client_data() functions
# =============================================================================
DUMMY_CALLER_DATA = {
    "FirstName": "John",
    "LastName": "Smith",
    "LoanID": "LN123456",
    "AccountNumberLastFour": "7890",
    "DOB": "1985-06-15",
    "TotalAmountDue": 2500.00,
    "TotalPaymentDue": 1200.00,
    "NextPaymentDueDate": "2025-01-15",
    "PropertyAddress": "123 Main St, Orlando, FL 32801",
    "LenderID": "LENDER001",
    "FeesBalance": 150.00,
    "AccountType": "checking",
    "RestrictAutoPayDraft": "N",
    "LastPaymentDate": "2024-12-01",
    "PaymentsOverdueCount": 2,
    "DaysLate": 45,
    "PrincipalBalance": 185000.00,
    "InterestRate": 6.5,
    "EscrowBalance": 3500.00,
    "MonthlyPayment": 1200.00
}

DUMMY_CLIENT_DATA = {
    "CompanyName": "Essex Mortgage",
    "LenderID": "LENDER001",
    "Phone": "+1234567890"
}
# =============================================================================
# END DUMMY DATA
# =============================================================================

#to convert value to float(Only for payment related fields)
def _safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning(f"Could not convert '{value}' to float, using default {default}")
        return default

#to convert value to int
def _safe_int(value, default=0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        logger.warning(f"Could not convert '{value}' to int, using default {default}")
        return default

async def fetch_caller_data(phone: str) -> Optional[Dict]:
    # --- DUMMY DATA CHECK: Remove this if block to use real API ---
    if os.getenv("USE_DUMMY_DATA", "false").lower() == "true":
        logger.info(f"Using dummy caller data for phone: {phone}")
        return DUMMY_CALLER_DATA
    # --- END DUMMY DATA CHECK ---

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{API_URL}/customerdaily/{API_USER}",
                params={"PhoneNumber": phone},
                headers={"Authorization": API_KEY}
            )

            response.raise_for_status()
            return response.json()

    except Exception as e:
        logger.error(f"Error: {e}")
        return None
    
async def fetch_client_data(lender_id: str) -> Optional[Dict]:
    # --- DUMMY DATA CHECK: Remove this if block to use real API ---
    if os.getenv("USE_DUMMY_DATA", "false").lower() == "true":
        logger.info(f"Using dummy client data for lender_id: {lender_id}")
        return DUMMY_CLIENT_DATA
    # --- END DUMMY DATA CHECK ---

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{API_URL}/clientlookup/{API_USER}",
                params={"LenderID": lender_id},
                headers={"Authorization": API_KEY}
            )

            response.raise_for_status()
            return response.json()

    except Exception as e:
        logger.error(f"Error: {e}")
        return None 

async def fetch_customer_data(caller: Dict) -> Optional[Dict]:
    return {
        "account_number": caller['AccountNumberLastFour'],
        "name": f"{caller['FirstName']} {caller['LastName']}",
        "first_name": caller['FirstName'],
        "last_name": caller['LastName'],
        "loan_number": caller['LoanID'],
        "amount_due": caller['TotalAmountDue'],
        "due_date": caller['NextPaymentDueDate'],
        "date_of_birth": caller['DOB'],
        "property_address": caller['PropertyAddress'], 
        "payment_status": {
            "total_payment_due": _safe_float(caller['TotalPaymentDue']),
            "fees_balance": _safe_float(caller['FeesBalance']),
            "total_amount_due": _safe_float(caller['TotalAmountDue']),
            "next_payment_due_date": caller['NextPaymentDueDate'],
            "account_last_four": caller['AccountNumberLastFour'],
            "account_type": caller.get('AccountType', 'checking'),  # Note:incorrectly set to TotalAmountDue
            "restrict_autopay_draft": caller['RestrictAutoPayDraft'],
            "last_payment_date": caller['LastPaymentDate'],
            "payments_overdue_count": _safe_int(caller['PaymentsOverdueCount']),
            "days_late": _safe_int(caller.get('DaysLate', 0)),  #dayslate parameter added which is missing
            "principal_balance": _safe_float(caller['PrincipalBalance']),
            "interest_rate": _safe_float(caller['InterestRate']),
            "escrow_balance": _safe_float(caller['EscrowBalance'])
        }
    }