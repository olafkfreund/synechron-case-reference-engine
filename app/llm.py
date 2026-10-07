import os

import litellm
from pydantic import BaseModel, ValidationError

from app.schema import llm_schema

litellm.turn_off_message_logging = True  # document text is never logged
MAX_TOKENS = 8192  # a full ReferenceCase with quotes; Bedrock's default may cut it off


def complete_json[M: BaseModel](alias: str, system: str, user: str, model_cls: type[M]) -> M:
    """alias is an env var name (EXTRACT_MODEL | DRAFT_MODEL) holding the LiteLLM model id."""
    model = os.environ.get(alias)
    if not model:
        raise RuntimeError(f"{alias} is not set; set it to a LiteLLM model id (e.g. bedrock/<model-id>)")
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=MAX_TOKENS,
        response_format={"type": "json_schema", "json_schema": {
            "name": model_cls.__name__, "schema": llm_schema(model_cls), "strict": True}},
    )
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise ValueError(f"{alias} reply truncated at max_tokens={MAX_TOKENS}")
    try:
        return model_cls.model_validate_json(choice.message.content or "")
    except ValidationError as e:
        # errors without input values: the reply quotes the document, and job errors are stored
        detail = e.errors(include_input=False, include_url=False)
        raise ValueError(f"{alias} reply is not a valid {model_cls.__name__}: {detail}") from None
