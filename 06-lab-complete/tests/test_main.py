import os
import pytest
from fastapi.testclient import TestClient

# Set environment variables for testing before importing settings
os.environ["AGENT_API_KEY"] = "test-secret-key"
os.environ["REDIS_URL"] = ""  # empty so it falls back to in-memory

from app.main import app, settings

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_ask_unauthorized():
    response = client.post("/ask", json={"question": "What is Docker?"})
    assert response.status_code == 401

def test_ask_authorized():
    headers = {"X-API-Key": "test-secret-key"}
    response = client.post(
        "/ask",
        json={"question": "What is Docker?", "session_id": "test-session"},
        headers=headers
    )
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert data["question"] == "What is Docker?"
