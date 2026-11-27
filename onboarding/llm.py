"""LLM access for onboarding: Azure OpenAI (the team's choice) or Claude (Anthropic) as an option.

Provider selection (`active_provider`):
  AGENTGUARD_PROVIDER=azure|anthropic forces one. Otherwise Azure is used as soon as any AZURE_OPENAI_* variable
  is filled in, Claude if only an Anthropic key exists, and Azure (the documented default) if nothing is set.

Azure settings come from .env (never printed): AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION (YYYY-MM-DD, default 2024-10-21).
Claude settings: ANTHROPIC_API_KEY, AGENTGUARD_MODEL, AGENTGUARD_EFFORT, AGENTGUARD_SERVER_FALLBACKS=0 to opt out.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ("azure", "anthropic")
LABELS = {"azure": "Azure OpenAI", "anthropic": "Claude"}
DEFAULT_AZURE_API_VERSION = "2024-10-21"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_OUTPUT_TOKENS = 16000


class LLMError(Exception):
    """The model call failed in a way a retry with feedback cannot fix."""
    kind = "api_error"


class MissingCredentials(LLMError):
    kind = "no_credentials"  # missing, blank or still a placeholder


class AuthFailed(LLMError):
    kind = "auth_failed"  # the service rejected the key or endpoint (401/403)


# --- environment -----------------------------------------------------------------------------

def _clean(value) -> str:
    """Strips whitespace and wrapping quotes: .env files often contain `KEY=" value "`."""
    return (value or "").strip().strip("\"'").strip()


def load_env() -> None:
    """Loads .env (never overriding real environment variables). A blank Anthropic key counts as missing."""
    load_dotenv(ROOT / ".env")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if name in os.environ and not os.environ[name].strip():
            del os.environ[name]


def env(name: str) -> str:
    return _clean(os.environ.get(name))


def provider_label(provider: str) -> str:
    return LABELS.get(provider, provider)


# --- Azure settings --------------------------------------------------------------------------

def azure_settings() -> dict:
    """Azure OpenAI settings, tolerant of common .env mistakes (see tests)."""
    endpoint = re.sub(r"(?<=[^/])/openai(?:/.*)?$", "", env("AZURE_OPENAI_ENDPOINT")).rstrip("/")
    version = env("AZURE_OPENAI_API_VERSION") or DEFAULT_AZURE_API_VERSION
    day_first = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})(-preview)?", version)
    if day_first:  # 21-10-2024 -> 2024-10-21
        version = f"{day_first[3]}-{day_first[2]}-{day_first[1]}{day_first[4] or ''}"
    return {"api_key": env("AZURE_OPENAI_API_KEY"), "endpoint": endpoint,
            "deployment": env("AZURE_OPENAI_DEPLOYMENT"), "api_version": version}


def azure_problems() -> list[str]:
    s = azure_settings()
    problems = []
    if not s["api_key"]:
        problems.append("AZURE_OPENAI_API_KEY is empty or missing")
    if not s["endpoint"]:
        problems.append("AZURE_OPENAI_ENDPOINT is empty or missing")
    elif "your-resource" in s["endpoint"].lower() or "<" in s["endpoint"]:
        problems.append("AZURE_OPENAI_ENDPOINT is still the example value; "
                        "use your resource's address, https://<resource-name>.openai.azure.com/")
    elif not s["endpoint"].startswith("https://"):
        problems.append("AZURE_OPENAI_ENDPOINT must start with https://")
    if not s["deployment"]:
        problems.append("AZURE_OPENAI_DEPLOYMENT is empty or missing (the name of your model deployment)")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(-preview)?", s["api_version"]):
        problems.append(f"AZURE_OPENAI_API_VERSION must look like 2024-10-21, got {s['api_version']!r}")
    return problems


def anthropic_problems() -> list[str]:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return []
    return ["ANTHROPIC_API_KEY is empty or missing"]


def active_provider() -> str:
    load_env()
    forced = env("AGENTGUARD_PROVIDER").lower()
    if forced in PROVIDERS:
        return forced
    if any(env(n) for n in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")):
        return "azure"
    return "anthropic" if not anthropic_problems() else "azure"


def credentials_problems(provider: str | None = None) -> list[str]:
    """What is missing or wrong in .env for the provider (empty list = looks usable). Never contains secrets."""
    load_env()
    provider = provider or active_provider()
    return azure_problems() if provider == "azure" else anthropic_problems()


def credentials_available(provider: str | None = None) -> bool:
    """True if the settings look usable. It does not check that the key is accepted by the service."""
    return not credentials_problems(provider)


def describe(provider: str) -> str:
    if provider == "azure":
        s = azure_settings()
        return f"azure deployment={s['deployment']!r} api_version={s['api_version']}"
    return f"anthropic model={_anthropic_model()} effort={_anthropic_effort()}"


# --- clients ---------------------------------------------------------------------------------

def make_client(provider: str):
    load_env()
    if provider == "azure":
        problems = azure_problems()
        if problems:
            raise MissingCredentials("; ".join(problems))
        from openai import AzureOpenAI
        s = azure_settings()
        return AzureOpenAI(azure_endpoint=s["endpoint"], api_key=s["api_key"],
                           api_version=s["api_version"], timeout=60.0)
    import anthropic
    return anthropic.Anthropic(timeout=60.0)  # may also resolve credentials from an `ant auth login` profile


def complete_json(provider: str, client, system: str, messages: list[dict], schema: dict) -> str:
    """One model call that must answer with JSON matching `schema`. Returns the JSON text.

    Raises LLMError (or a subclass) for anything a retry with validator feedback cannot fix.
    """
    if provider == "azure":
        return _azure_complete(client, system, messages, schema)
    return _anthropic_complete(client, system, messages, schema)


# --- Azure OpenAI ----------------------------------------------------------------------------

def _short(e: Exception) -> str:
    return str(getattr(e, "message", None) or e)[:300]


def _azure_complete(client, system: str, messages: list[dict], schema: dict) -> str:
    import openai

    from .policy_schema import to_strict_schema

    s = azure_settings()
    structured = {"type": "json_schema",
                  "json_schema": {"name": "policy_rules", "strict": True, "schema": to_strict_schema(schema)}}
    use_schema, tokens_param = True, "max_completion_tokens"
    response = None
    for _ in range(4):  # a deployment may lack structured outputs, or need max_tokens instead
        kwargs = {"model": s["deployment"], "messages": [{"role": "system", "content": system}, *messages],
                  "response_format": structured if use_schema else {"type": "json_object"},
                  tokens_param: MAX_OUTPUT_TOKENS}
        try:
            response = client.chat.completions.create(**kwargs)
            break
        except openai.BadRequestError as e:
            text = str(e)
            if use_schema and re.search(r"response_format|json_schema|structured", text, re.I):
                use_schema = False
                continue
            if tokens_param == "max_completion_tokens" and "max_completion_tokens" in text:
                tokens_param = "max_tokens"
                continue
            raise LLMError(f"Azure OpenAI rejected the request (400): {_short(e)}") from e
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            raise AuthFailed(f"Azure OpenAI refused the credentials ({e.status_code}): "
                             "check AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT") from e
        except openai.NotFoundError as e:
            raise LLMError(f"Azure OpenAI found nothing at deployment '{s['deployment']}' (404). "
                           f"Check AZURE_OPENAI_DEPLOYMENT, the endpoint and AZURE_OPENAI_API_VERSION: {_short(e)}") from e
        except openai.APIStatusError as e:
            raise LLMError(f"Azure OpenAI error {e.status_code}: {_short(e)}") from e
        except openai.APIConnectionError as e:  # includes timeouts
            raise LLMError(f"Could not reach Azure OpenAI at {s['endpoint']}: {_short(e)}") from e
    if response is None:
        raise LLMError("Azure OpenAI rejected every request variant")

    choice = response.choices[0]
    if getattr(choice.message, "refusal", None):
        raise LLMError("The model refused the request")
    if choice.finish_reason == "length":
        raise LLMError("The model's answer was cut off (max tokens)")
    if choice.finish_reason == "content_filter":
        raise LLMError("Azure's content filter blocked the answer")
    if not choice.message.content:
        raise LLMError("The model returned no text")
    return choice.message.content


# --- Claude (Anthropic) ----------------------------------------------------------------------

def _anthropic_model() -> str:
    return os.environ.get("AGENTGUARD_MODEL", DEFAULT_ANTHROPIC_MODEL)


def _anthropic_effort() -> str:
    return os.environ.get("AGENTGUARD_EFFORT", "medium")


def _anthropic_complete(client, system: str, messages: list[dict], schema: dict) -> str:
    import anthropic

    kwargs = dict(
        model=_anthropic_model(),
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=messages,
        output_config={"effort": _anthropic_effort(), "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        if os.environ.get("AGENTGUARD_SERVER_FALLBACKS", "1") != "0":
            # Refusal safety net for claude-opus-5-5: re-runs the request on a fallback model server-side.
            response = client.beta.messages.create(betas=[ANTHROPIC_FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise AuthFailed(f"Claude API refused the credentials ({e.status_code})") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:  # includes timeouts
        raise LLMError(f"Could not reach the Claude API: {e}") from e
    except TypeError as e:  # the SDK raises TypeError when no credentials can be resolved
        if "authentication" in str(e).lower():
            raise MissingCredentials("No Anthropic credentials found (set ANTHROPIC_API_KEY in .env)") from e
        raise

    if response.stop_reason == "refusal":
        raise LLMError("Claude refused the request")
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude's answer was cut off (max_tokens)")
    for block in response.content:
        if block.type == "text":
            return block.text
    raise LLMError("Claude returned no text")
