import httpx
import logging
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


_config = None


def load_config(config_path: str = "output_3_minimal.json") -> Dict:
    global _config
    
    if _config:
        return _config
    
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
            _config = data.get("memo_api", {}).get("apis", {})
            
            # Replace environment variables in api_key
            for api_type in _config:
                if "api_key" in _config[api_type]:
                    api_key = _config[api_type]["api_key"]
                    if api_key.startswith("${") and api_key.endswith("}"):
                        env_var = api_key[2:-1]
                        _config[api_type]["api_key"] = os.getenv(env_var, "dev_token")
            
            logger.info(f"Loaded memo API config from {config_path}")
            return _config
            
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}


def get_api_config(api_type: str = "aimemo") -> tuple:
    """Get API configuration"""
    config = load_config()
    api = config.get(api_type, {})
    return (
        api.get("base_url", ""),
        api.get("user_id", ""),
        api.get("api_key", "")
    )


async def post_memo(memo_data: Dict[str, Any]) -> Optional[Dict]:

    try:
        base_url, user_id, api_key = get_api_config("aimemo")
        
        if not all([base_url, user_id, api_key]):
            logger.error("Memo API configuration incomplete")
            return None
        

        required_fields = [
            "Loan_ID", "Subject", "Date_Time", "Category",
            "User", "Notify_on_Date", "Code", "ConversationID"
        ]
        
        missing = [f for f in required_fields if not memo_data.get(f)]
        if missing:
            logger.error(f"Missing required fields: {missing}")
            return None

        memo_payload = memo_data.copy()
        if isinstance(memo_payload.get("Date_Time"), datetime):
            memo_payload["Date_Time"] = memo_payload["Date_Time"].isoformat()
        
        logger.info(f"Posting memo for Loan {memo_data.get('Loan_ID')}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/{user_id}",
                json=memo_payload,
                headers={"Authorization": api_key}
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Memo posted successfully: {result.get('confirmation_id')}")
            return result
            
    except Exception as e:
        logger.error(f"Memo API error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def post_memo_sync(memo_data: Dict[str, Any]) -> Optional[Dict]:
        
    try:

        base_url, user_id, api_key = get_api_config("aimemo")
        
        if not all([base_url, user_id, api_key]):
            logger.error("Memo API configuration incomplete")
            return None
        
        memo_payload = memo_data.copy()
        if isinstance(memo_payload.get("Date_Time"), datetime):
            memo_payload["Date_Time"] = memo_payload["Date_Time"].isoformat()
        
        logger.info(f"Posting memo for Loan {memo_data.get('Loan_ID')}")
        
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{base_url}/{user_id}",
                json=memo_payload,
                headers={"Authorization": api_key}
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Memo posted successfully")
            return result
            
    except Exception as e:
        logger.error(f"Memo API error: {e}")
        return None