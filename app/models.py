from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class WebhookRequest(BaseModel):
    user_id: str = Field(..., description="Unique identifier for the user / café owner", min_length=1)
    message: str = Field(..., description="Natural language message or editing instruction", min_length=1)


class WebhookResponse(BaseModel):
    status: str = Field(..., description="'pending' | 'updated' | 'no_change' | 'rolled_back' | 'error' | 'rejected'")
    action: str = Field(..., description="'edit' | 'chat' | 'undo' | 'rejected'")
    reply: str = Field(..., description="Helpful, polite reply for the user")
    version: int = Field(..., description="Current published website version")
    draft_id: Optional[str] = Field(None, description="Draft ID if an edit was proposed")
    preview_url: Optional[str] = Field(None, description="URL endpoint to preview the proposed draft")
    change_summary: Optional[str] = Field(None, description="AI/Diff generated summary of proposed changes")
    diff: Optional[str] = Field(None, description="HTML diff snippet")
    validation_errors: Optional[List[str]] = Field(None, description="List of QA validation failure messages if rejected")


class ApproveRequest(BaseModel):
    draft_id: str = Field(..., description="Unique ID of the pending draft to approve and publish", min_length=1)


class ApproveResponse(BaseModel):
    status: str = "approved"
    message: str
    version: int
    draft_id: str


class RejectRequest(BaseModel):
    draft_id: Optional[str] = Field(None, description="Optional ID of draft to reject (defaults to current pending draft)")


class RejectResponse(BaseModel):
    status: str = "rejected"
    message: str
    version: int
    draft_id: str


class PreviewResponse(BaseModel):
    draft_id: str
    user_id: str
    user_instruction: str
    instruction: str
    original_html: str
    proposed_html: str
    html_diff: str
    diff: str
    change_summary: str
    validation_results: Dict[str, Any]
    base_version: int
    created_at: str
    status: str
    structured_changes: Optional[Dict[str, Any]] = None


class LoginRequest(BaseModel):
    email: str = Field(..., description="Administrator email address")
    password: str = Field(..., description="Administrator password")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="Current password")
    new_password: str = Field(..., description="New password (minimum 6 characters)", min_length=6)


class CreateConversationRequest(BaseModel):
    title: Optional[str] = Field("New Conversation", description="Title of the conversation")


class ChatMessageRequest(BaseModel):
    message: str = Field(..., description="Instruction or message sent to the AI website editor", min_length=1)

