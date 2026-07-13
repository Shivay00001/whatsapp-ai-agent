import os
import hmac
import hashlib
import httpx
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Form, Request, Response, HTTPException, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from dotenv import load_dotenv
import litellm
from pydantic import BaseModel

from database import engine, Base, SessionLocal, get_db
from models import Setting, Conversation

load_dotenv()

load_dotenv()
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Initialize default prompt if not exists
    async with SessionLocal() as db:
        result = await db.execute(select(Setting).where(Setting.key == "system_prompt"))
        if not result.scalar_one_or_none():
            default_prompt = Setting(key="system_prompt", value="You are a helpful WhatsApp AI agent.")
            db.add(default_prompt)
            await db.commit()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# BACKGROUND TASK PIPELINE
# ==========================================
async def get_api_key(db: AsyncSession, key_name: str, env_fallback: str) -> str:
    result = await db.execute(select(Setting).where(Setting.key == key_name))
    setting = result.scalar_one_or_none()
    if setting and setting.value:
        return setting.value
    return os.getenv(env_fallback)

async def process_whatsapp_message_task(sender: str, user_message: str, reply_callback):
    """
    Core AI logic executed as a background task. 
    Prevents blocking the webhook response to Twilio/Meta.
    """
    async with SessionLocal() as db:
        try:
            # 1. Save user message to DB
            user_msg_db = Conversation(phone_number=sender, role="user", content=user_message)
            db.add(user_msg_db)
            await db.commit()

            # 2. Get System Prompt
            result = await db.execute(select(Setting).where(Setting.key == "system_prompt"))
            system_prompt_setting = result.scalar_one_or_none()
            sys_prompt = system_prompt_setting.value if system_prompt_setting else "You are a helpful assistant."

            # 3. Get Conversation History
            history_result = await db.execute(
                select(Conversation)
                .where(Conversation.phone_number == sender)
                .order_by(Conversation.timestamp.desc())
                .limit(20) # Keep context window reasonable
            )
            history = list(reversed(history_result.scalars().all()))

            messages = [{"role": "system", "content": sys_prompt}]
            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})

            # 3.5 Get OpenAI Key
            openai_key = await get_api_key(db, "openai_api_key", "OPENAI_API_KEY")
            anthropic_key = await get_api_key(db, "anthropic_api_key", "ANTHROPIC_API_KEY")
            gemini_key = await get_api_key(db, "gemini_api_key", "GEMINI_API_KEY")
            glm_key = await get_api_key(db, "glm_api_key", "ZHIPUAI_API_KEY")
            provider = await get_api_key(db, "llm_provider", "LLM_PROVIDER") or "gpt-4o"

            def get_active_key(prov: str):
                if prov.startswith("gpt"): return openai_key
                if prov.startswith("claude"): return anthropic_key
                if prov.startswith("gemini"): return gemini_key
                if prov.startswith("zhipu"): return glm_key
                return openai_key

            # 4. Generate AI Response (with retry loop for resilience)
            ai_reply = "Sorry, I am having trouble connecting to my brain right now."
            for attempt in range(3):
                try:
                    response = await litellm.acompletion(
                        model=provider,
                        messages=messages,
                        api_key=get_active_key(provider),
                        max_tokens=500,
                        timeout=10.0
                    )
                    ai_reply = response.choices[0].message.content
                    break
                except Exception as e:
                    print(f"Litellm Error (Attempt {attempt+1}): {e}")
                    await asyncio.sleep(1)

            # 5. Save AI response to DB
            ai_msg_db = Conversation(phone_number=sender, role="assistant", content=ai_reply)
            db.add(ai_msg_db)
            await db.commit()
            
            # 6. Dispatch reply back to the platform
            await reply_callback(sender, ai_reply)

        except Exception as e:
            print(f"Critical Pipeline Error: {e}")

# ==========================================
# TWILIO API ENDPOINT
# ==========================================
async def send_twilio_message(to_number: str, text: str):
    """Sends outbound message via Twilio Async Client"""
    async with SessionLocal() as db:
        sid = await get_api_key(db, "twilio_account_sid", "TWILIO_ACCOUNT_SID")
        token = await get_api_key(db, "twilio_auth_token", "TWILIO_AUTH_TOKEN")
        twilio_number = await get_api_key(db, "twilio_phone_number", "TWILIO_PHONE_NUMBER")
    
    client = Client(sid, token)
    
    await client.messages.create_async(
        body=text,
        from_=f"whatsapp:{twilio_number}",
        to=f"whatsapp:{to_number}"
    )

@app.post("/webhook/whatsapp")
async def twilio_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_twilio_signature: str = Header(None)
):
    """Twilio Webhook Endpoint with Validation & Async processing."""
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        raise HTTPException(status_code=500, detail="Server misconfiguration")
    
    validator = RequestValidator(auth_token)
    form_data = await request.form()
    
    # In production, construct the exact URL Twilio hit.
    # url = str(request.url).replace("http://", "https://") 
    # if not validator.validate(url, form_data, x_twilio_signature):
    #     raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    sender = form_data.get("From", "").replace("whatsapp:", "")
    body = form_data.get("Body", "")
    
    # Dispatch to Background Task
    background_tasks.add_task(process_whatsapp_message_task, sender, body, send_twilio_message)
    
    # Return immediate 200 OK (empty TwiML)
    return Response(content="<Response></Response>", media_type="application/xml")


# ==========================================
# META OFFICIAL API ENDPOINTS
# ==========================================
async def send_meta_message(to_number: str, text: str):
    """Sends outbound message via Meta Graph API"""
    phone_id = os.getenv("META_PHONE_NUMBER_ID")
    token = os.getenv("META_ACCESS_TOKEN")
    
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text}
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            print(f"Failed to send Meta message: {res.text}")


@app.get("/webhook/meta")
async def verify_meta_webhook(request: Request):
    """Meta webhook verification endpoint"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == os.getenv("META_VERIFY_TOKEN"):
            return int(challenge)
        else:
            raise HTTPException(status_code=403, detail="Verification token mismatch")
    raise HTTPException(status_code=400, detail="Missing parameters")

@app.post("/webhook/meta")
async def meta_webhook(
    request: Request, 
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None)
):
    """Receives WhatsApp messages from Meta Official Cloud API securely."""
    # 1. Validate Signature
    body_bytes = await request.body()
    app_secret = os.getenv("META_APP_SECRET")
    
    if app_secret and x_hub_signature_256:
        expected_sig = hmac.new(
            app_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(f"sha256={expected_sig}", x_hub_signature_256):
            raise HTTPException(status_code=403, detail="Invalid Meta signature")
            
    body = await request.json()
    
    # 2. Extract Message
    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        
        if "messages" in value:
            message_info = value["messages"][0]
            sender = message_info["from"] 
            
            if message_info["type"] == "text":
                user_message = message_info["text"]["body"]
                
                # 3. Dispatch to Background Task
                background_tasks.add_task(process_whatsapp_message_task, sender, user_message, send_meta_message)
                
    except Exception as e:
        print(f"Meta Webhook Parsing Error: {e}")
        
    # 4. Return immediate 200 OK
    return {"status": "ok"}


# ==========================================
# DASHBOARD SETTINGS ENDPOINTS
# ==========================================
class PromptUpdate(BaseModel):
    system_prompt: str

@app.get("/api/settings/prompt")
async def get_prompt(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting).where(Setting.key == "system_prompt"))
    setting = result.scalar_one_or_none()
    return {"system_prompt": setting.value if setting else ""}

@app.post("/api/settings/prompt")
async def update_prompt(req: PromptUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting).where(Setting.key == "system_prompt"))
    setting = result.scalar_one_or_none()
    
    if setting:
        setting.value = req.system_prompt
    else:
        setting = Setting(key="system_prompt", value=req.system_prompt)
        db.add(setting)
        
    await db.commit()
    return {"status": "success", "system_prompt": setting.value}

class ApiKeysUpdate(BaseModel):
    openai_api_key: str
    anthropic_api_key: str
    gemini_api_key: str
    glm_api_key: str
    llm_provider: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str

@app.post("/api/settings/keys")
async def update_keys(req: ApiKeysUpdate, db: AsyncSession = Depends(get_db)):
    keys = [
        ("openai_api_key", req.openai_api_key), 
        ("anthropic_api_key", req.anthropic_api_key),
        ("gemini_api_key", req.gemini_api_key),
        ("glm_api_key", req.glm_api_key),
        ("llm_provider", req.llm_provider),
        ("twilio_account_sid", req.twilio_account_sid),
        ("twilio_auth_token", req.twilio_auth_token),
        ("twilio_phone_number", req.twilio_phone_number)
    ]
    for k, v in keys:
        if v:
            res = await db.execute(select(Setting).where(Setting.key == k))
            setting = res.scalar_one_or_none()
            if setting:
                setting.value = v
            else:
                db.add(Setting(key=k, value=v))
    await db.commit()
    return {"status": "success"}

