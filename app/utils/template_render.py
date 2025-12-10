"""
Template Renderer - Process template tags and variable substitution in prompts.

Handles:
- Conditional blocks: {% tag %} ... {% endtag %}
- Variable substitution: {{variable}}
"""

import re
import logging
from typing import Dict, Any, Callable
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Conditional tag mappings - each returns True if block should be KEPT
CONDITIONAL_MAPPINGS: Dict[str, Callable[[Dict], bool]] = {
    # Language conditionals
    "en": lambda ctx: ctx.get("language", "en") == "en",
    "es": lambda ctx: ctx.get("language", "en") == "es",
    "english_examples": lambda ctx: ctx.get("language", "en") == "en",
    "spanish_examples": lambda ctx: ctx.get("language", "en") == "es",
    
    # Account availability
    "essex_loan_acct_available": lambda ctx: bool(ctx.get("AccountNumberLastFour")),
    "essex_loan_acct_unavailable": lambda ctx: not ctx.get("AccountNumberLastFour"),
    
    # Payment date (today vs future)
    "upd_current_dated_payment": lambda ctx: _is_payment_today(ctx),
    "upd_future_dated_payment": lambda ctx: not _is_payment_today(ctx),
    
    # Certified funds restriction
    "RestrictAutoPayDraft": lambda ctx: ctx.get("RestrictAutoPayDraft") == "Y",
    "NoRestrictAutoPayDraft": lambda ctx: ctx.get("RestrictAutoPayDraft") != "Y",
    
    # Days late thresholds
    "days_late_leq_15": lambda ctx: int(ctx.get("DaysLate", 0)) <= 15,
    "days_late_gt_15": lambda ctx: int(ctx.get("DaysLate", 0)) > 15,
    "days_late_gt_30": lambda ctx: int(ctx.get("DaysLate", 0)) > 30,
    "days_late_gt_45": lambda ctx: int(ctx.get("DaysLate", 0)) > 45,
    "days_late_leq_45": lambda ctx: int(ctx.get("DaysLate", 0)) <= 45,
    
    # Birthday/Anniversary/Veteran
    "is_birthday": lambda ctx: ctx.get("is_birthday", False),
    "is_anniversary": lambda ctx: ctx.get("is_anniversary", False),
    "is_veteran": lambda ctx: ctx.get("is_veteran", False),
    "not_birthday": lambda ctx: not ctx.get("is_birthday", False),
    "not_anniversary": lambda ctx: not ctx.get("is_anniversary", False),
    
    # Prompt ordering (first vs reprompt)
    "firstprompt": lambda ctx: ctx.get("prompt_count", 0) == 0,
    "reprompt": lambda ctx: ctx.get("prompt_count", 0) > 0,
    
    # Appointment handling
    "user_appt_conflict": lambda ctx: ctx.get("appt_conflict", False),
    "no_appt_conflict": lambda ctx: not ctx.get("appt_conflict", False),
    
    # Name matching (for co-borrower scenarios)
    "name_match": lambda ctx: ctx.get("name_match", False),
    "name_no_match": lambda ctx: not ctx.get("name_match", False),
    
    # Bank account scenarios
    "has_existing_account": lambda ctx: bool(ctx.get("AccountNumberLastFour")),
    "no_existing_account": lambda ctx: not ctx.get("AccountNumberLastFour"),
    "using_new_account": lambda ctx: ctx.get("new_bank_account_confirmed", False),
    "using_existing_account": lambda ctx: ctx.get("existing_bank_account_confirmed", False),
    
    # Payment method
    "payment_method_checking": lambda ctx: ctx.get("new_account_payment_method") == "checking",
    "payment_method_savings": lambda ctx: ctx.get("new_account_payment_method") == "savings",
    
    # Disaster impact
    "disaster_affected": lambda ctx: ctx.get("affected_by_disaster", False),
    "not_disaster_affected": lambda ctx: not ctx.get("affected_by_disaster", False),
    
    # Transfer scenarios
    "transfer_reason_provided": lambda ctx: bool(ctx.get("transfer_reason")),
    
    # DOB verification
    "dob_attempt_1": lambda ctx: ctx.get("dob_attempts", 0) == 1,
    "dob_attempt_2": lambda ctx: ctx.get("dob_attempts", 0) >= 2,
    
    # Fees
    "has_fees": lambda ctx: float(ctx.get("FeesBalance", 0) or 0) > 0,
    "no_fees": lambda ctx: float(ctx.get("FeesBalance", 0) or 0) <= 0,
}


def _is_payment_today(ctx: Dict) -> bool:
    """Check if payment date is today."""
    # Check both variable names for compatibility (n67 extracts user_provided_payment_date)
    payment_date = ctx.get("upd_extracted_payment_date") or ctx.get("user_provided_payment_date")
    if not payment_date:
        return False

    # Handle category strings like "today", "tonight", "end of day"
    today_categories = ["today", "tonight", "end of day", "by the end of the day"]
    if payment_date.lower() in today_categories:
        return True

    today = ctx.get("current_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return payment_date == today


def render_template(template: str, context: Dict[str, Any]) -> str:
    """
    Main entry point - process conditionals and substitute variables.

    Args:
        template: Raw template string with {% %} and {{ }} tags
        context: Context dictionary with variable values

    Returns:
        Fully rendered prompt string
    """
    if not template:
        return ""

    # Debug: Log first 100 chars of template
    logger.info(f"🔧 [TEMPLATE RENDER] Input starts with: {template[:100]}...")

    # Step 1: Process conditional blocks
    rendered = process_conditionals(template, context)

    # Debug: Log first 100 chars after conditionals
    logger.info(f"🔧 [TEMPLATE RENDER] After conditionals: {rendered[:100]}...")

    # Step 2: Substitute variables
    rendered = substitute_variables(rendered, context)

    # Step 3: Clean up extra whitespace
    rendered = clean_whitespace(rendered)

    # Debug: Log first 100 chars after cleanup
    logger.info(f"🔧 [TEMPLATE RENDER] After cleanup: {rendered[:100]}...")

    return rendered


def process_conditionals(template: str, context: Dict[str, Any]) -> str:
    """
    Process {% tag %} ... {% endtag %} conditional blocks.
    
    Keeps blocks where condition is True, removes blocks where condition is False.
    
    Args:
        template: Template string with conditional blocks
        context: Context dictionary
        
    Returns:
        Template with conditionals resolved
    """
    result = template
    
    # Find all unique tag names in the template
    tag_pattern = r'\{%\s*(\w+)\s*%\}'
    tags_found = set(re.findall(tag_pattern, template))
    
    # Process each tag
    for tag_name in tags_found:
        # Skip 'end' tags - they'll be handled with their opening tag
        if tag_name.startswith('end'):
            continue
            
        # Check if we have a mapping for this tag
        if tag_name in CONDITIONAL_MAPPINGS:
            should_keep = CONDITIONAL_MAPPINGS[tag_name](context)
            
            if should_keep:
                result = keep_block(result, tag_name)
            else:
                result = remove_block(result, tag_name)
        else:
            # Unknown tag - keep the content, remove the tags
            logger.warning(f"Unknown conditional tag: {tag_name}")
            result = keep_block(result, tag_name)
    
    return result


def keep_block(template: str, tag_name: str) -> str:
    """
    Keep the content between tags, but remove the tags themselves.
    
    {% tag_name %} content here {% endtag_name %} -> content here
    
    Args:
        template: Template string
        tag_name: Name of the tag (without {% %})
        
    Returns:
        Template with tags removed but content kept
    """
    # Pattern to match opening and closing tags
    pattern = r'\{%\s*' + re.escape(tag_name) + r'\s*%\}(.*?)\{%\s*end' + re.escape(tag_name) + r'\s*%\}'
    
    # Replace tags but keep inner content
    result = re.sub(pattern, r'\1', template, flags=re.DOTALL)
    
    return result


def remove_block(template: str, tag_name: str) -> str:
    """
    Remove the entire block including tags and content.
    
    {% tag_name %} content here {% endtag_name %} -> (empty)
    
    Args:
        template: Template string
        tag_name: Name of the tag (without {% %})
        
    Returns:
        Template with entire block removed
    """
    # Pattern to match opening tag, content, and closing tag
    pattern = r'\{%\s*' + re.escape(tag_name) + r'\s*%\}.*?\{%\s*end' + re.escape(tag_name) + r'\s*%\}'
    
    # Remove entire block
    result = re.sub(pattern, '', template, flags=re.DOTALL)
    
    return result


def substitute_variables(template: str, context: Dict[str, Any]) -> str:
    """
    Replace {{variable}} placeholders with actual values.
    
    Args:
        template: Template string with {{variable}} placeholders
        context: Context dictionary with values
        
    Returns:
        Template with variables substituted
    """
    def replace_var(match):
        var_name = match.group(1).strip()
        value = context.get(var_name)
        
        if value is None:
            logger.debug(f"Variable not found in context: {var_name}")
            return ""  # Return empty string for missing variables
        
        return str(value)
    
    # Pattern to match {{variable_name}}
    pattern = r'\{\{\s*(\w+)\s*\}\}'
    
    result = re.sub(pattern, replace_var, template)
    
    return result


def clean_whitespace(text: str) -> str:
    """
    Clean up extra whitespace from template processing.
    
    Args:
        text: Text with potential extra whitespace
        
    Returns:
        Cleaned text
    """
    # Replace multiple newlines with double newline
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Remove leading/trailing whitespace from each line while preserving structure
    lines = text.split('\n')
    cleaned_lines = [line.strip() if line.strip() == '' else line for line in lines]
    
    # Remove leading/trailing empty lines
    while cleaned_lines and cleaned_lines[0].strip() == '':
        cleaned_lines.pop(0)
    while cleaned_lines and cleaned_lines[-1].strip() == '':
        cleaned_lines.pop()
    
    return '\n'.join(cleaned_lines)


def get_available_variables(template: str) -> list:
    """
    Extract list of variable names used in template.
    
    Args:
        template: Template string
        
    Returns:
        List of variable names
    """
    pattern = r'\{\{\s*(\w+)\s*\}\}'
    return list(set(re.findall(pattern, template)))


def get_conditional_tags(template: str) -> list:
    """
    Extract list of conditional tags used in template.
    
    Args:
        template: Template string
        
    Returns:
        List of tag names (excluding 'end' tags)
    """
    pattern = r'\{%\s*(\w+)\s*%\}'
    tags = set(re.findall(pattern, template))
    return [t for t in tags if not t.startswith('end')]


def validate_template(template: str) -> Dict[str, Any]:
    """
    Validate a template for common issues.
    
    Args:
        template: Template string to validate
        
    Returns:
        Dictionary with validation results
    """
    issues = []
    
    # Check for unclosed tags
    tag_pattern = r'\{%\s*(\w+)\s*%\}'
    tags = re.findall(tag_pattern, template)
    
    open_tags = [t for t in tags if not t.startswith('end')]
    close_tags = [t.replace('end', '') for t in tags if t.startswith('end')]
    
    for tag in open_tags:
        if tag not in close_tags:
            issues.append(f"Unclosed tag: {tag}")
    
    for tag in close_tags:
        if tag not in open_tags:
            issues.append(f"Closing tag without opening: end{tag}")
    
    # Check for unknown conditional tags
    for tag in open_tags:
        if tag not in CONDITIONAL_MAPPINGS:
            issues.append(f"Unknown conditional tag (will default to keep): {tag}")
    
    # Get variables and tags
    variables = get_available_variables(template)
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "variables_used": variables,
        "conditional_tags": open_tags
    }


# Convenience function for API body substitution
def substitute_api_body(body_items: list, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Substitute variables in API body definition.
    
    Args:
        body_items: List of {"key": "...", "value": "{{var}}"} items
        context: Context dictionary
        
    Returns:
        Dictionary ready for API call
    """
    result = {}
    
    for item in body_items:
        key = item.get("key")
        value_template = item.get("value", "")
        
        # Substitute variables in value
        value = substitute_variables(value_template, context)
        
        # Try to convert to appropriate type
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.isdigit():
            value = int(value)
        else:
            try:
                value = float(value)
            except ValueError:
                pass  # Keep as string
        
        result[key] = value
    
    return result