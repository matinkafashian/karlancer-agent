import inspect

from karlancer_agent.proposal.generator import ProposalGenerator


def test_llm_proposal_generator_has_no_browser_or_page_dependency() -> None:
    annotations = str(inspect.signature(ProposalGenerator.build))
    module_names = set(ProposalGenerator.__dict__)
    assert "Page" not in annotations
    assert "playwright" not in annotations.lower()
    assert not {"click", "locator", "goto"} & module_names
