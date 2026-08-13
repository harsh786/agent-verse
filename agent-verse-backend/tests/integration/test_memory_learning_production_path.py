from app.main import create_app
from app.memory.repository import InMemoryMemoryRepository


def test_memory_learning_services_are_explicitly_wired_for_test_path() -> None:
    app = create_app(manage_pools=False)
    assert isinstance(app.state.memory_repository, InMemoryMemoryRepository)
    assert app.state.reflexion_service is not None
    assert app.state.prospective_memory_service is not None
    assert app.state.learning_experiment_service is not None
    assert app.state.improvement_action_executor is not None
