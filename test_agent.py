import os, json
from app.services.agent_service import AgentWorkflow
from app.models.schemas import UserProfile

class TestWorkflow(AgentWorkflow):
    def _call_model(self, prompt: str):
        class DummyResp:
            def __init__(self, text):
                self.text = text
        # Simulate a response that returns a JSON string literal
        # For market, architect, curator, critic steps we will always return same bad response
        # We'll make it a JSON string that is just a message (string)
        bad_json = json.dumps('just a string')
        return DummyResp(bad_json)

profile = UserProfile(name='Test', current_role='Student', target_role='Developer', current_skills=['Python'], preferred_style='Video')

wf = TestWorkflow()
try:
    result = wf.generate_learning_path_sync(profile)
    print('Result:', result)
except Exception as e:
    print('Error:', e)
