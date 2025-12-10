import httpx
import logging
import json
import os
from typing import Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_config = None


def load_config(config_path: str = "outbound_config.json") -> Dict:
    global _config
    
    if _config:
        return _config
    
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
            _config = data.get("collection_activity", {}).get("apis", {})
            for api_type in _config:
                if "api_key" in _config[api_type]:
                    api_key = _config[api_type]["api_key"]
                    if api_key.startswith("${") and api_key.endswith("}"):
                        env_var = api_key[2:-1]
                        _config[api_type]["api_key"] = os.getenv(env_var, "dev_token")
            
            logger.info(f"Loaded collection activity API config from {config_path}")
            return _config
            
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}


def get_api_config(api_type: str = "ailastcollectiondate") -> tuple:
    config = load_config()
    api = config.get(api_type, {})
    return (
        api.get("base_url", ""),
        api.get("user_id", ""),
        api.get("api_key", ""),
        api.get("timeout", 30)
    )


def post_collection_activity(fics_loan_number: int) -> Optional[Dict]:

    try:
        base_url, user_id, api_key, timeout = get_api_config("ailastcollectiondate")
        
        if not all([base_url, user_id, api_key]):
            logger.error("Collection activity API configuration incomplete")
            return None
        
        collection_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        logger.info(f"Recording collection activity for loan {fics_loan_number}")
        
        with httpx.Client(timeout=float(timeout)) as client:
            response = client.post(
                f"{base_url}/{user_id}",
                json={
                    "FICS_loan_number": fics_loan_number,
                    "collection_activity_date": collection_date,
                    "user": "FICSAPI"
                },
                headers={"Authorization": api_key}
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Collection activity recorded successfully for loan {fics_loan_number}")
            return result
            
    except Exception as e:
        logger.error(f"Collection activity API error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None