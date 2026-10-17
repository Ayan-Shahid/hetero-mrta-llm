"""LLM layer: provider-agnostic client, baseline LLM methods and the planning agent."""

from .agent import run_agent
from .client import LLMConfig, make_client
from .methods import MethodResult, run_direct, run_formulate, run_pefa

LLM_METHODS = {
    "llm-direct": run_direct,
    "llm-pefa": run_pefa,
    "llm-formulate": run_formulate,
    "agent": run_agent,
}

__all__ = ["LLM_METHODS", "LLMConfig", "MethodResult", "make_client", "run_agent", "run_direct",
           "run_formulate", "run_pefa"]
