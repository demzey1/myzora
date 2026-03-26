# Phase 2: Conversational AI-Assisted Trading Bot

## Overview

The Zora Signal Bot has been transformed from a **command-driven** bot to a **conversational AI-assisted Telegram trading assistant**. Users can now interact with the bot using natural language without relying on slash commands for normal usage.

## Architecture

### 4-Layer Design

```
┌─────────────────────────────────────────────────────────────────┐
│ MESSAGING LAYER                                                 │
│ • Telegram Bot (python-telegram-bot 21.6)                      │
│ • Webhook + Polling modes                                      │
│ • Command handlers + free-text message routing                 │
└─────────────────────────────────────────────────────────────────┘

         ↓

┌─────────────────────────────────────────────────────────────────┐
│ ASSISTANT ORCHESTRATION LAYER (NEW - Phase 2)                  │
│ • OpenAI Responses API (Assistants v2)                         │
│ • Per-user conversation threads                                │
│ • Tool iteration & result submission                           │
│ • Multi-turn conversation state management                     │
└─────────────────────────────────────────────────────────────────┘

         ↓

┌─────────────────────────────────────────────────────────────────┐
│ DOMAIN SERVICES LAYER (NEW - Phase 2)                          │
│ • Tool Executor (ToolExecutor class)                           │
│ • Real service wiring (not mocked)                             │
│ • Repositories, scoring engine, wallet linking                 │
└─────────────────────────────────────────────────────────────────┘

         ↓

┌─────────────────────────────────────────────────────────────────┐
│ DATA & INTEGRATIONS LAYER                                       │
│ • SQLAlchemy models (async)                                    │
│ • External APIs (SocialData, Zora, Alchemy)                    │
│ • PostgreSQL, Redis, Celery                                    │
└─────────────────────────────────────────────────────────────────┘
```

## User Experience

### Before (Command-Driven)
```
User: /addaccount vitalik
User: /signals
User: /positions
User: /approve_paper 42
```

### After (Conversational)
```
User: "track vitalik for zora signals"
Bot: ✅ Now tracking @vitalik in hybrid mode. I'll watch for trading opportunities.

User: "show me bullish signals"
Bot: [Lists recent high-scoring signals with inline [Buy] [Cancel] buttons]

User: "why did you flag this coin?"
Bot: 📊 Score Breakdown showing deterministic + LLM scores, recommendation reasoning

User: "buy the top signal with 0.01 ETH"
Bot: Preview with price, slippage, fees
      [✅ BUY] [❌ Cancel]

User: "what's my position in TEST?"
Bot: [Lists open position with PnL, [Close Position] button]
```

## Key Components

### 1. OpenAI Responses API Client (`app/integrations/openai_responses_client.py`)

**Purpose**: Wrapper around OpenAI Assistants v2 API

**Features**:
- Thread management (one per user)
- Assistant with 11 integrated tools
- Async HTTP client with retry logic (tenacity)
- Tool schema definitions

**Tools Defined**:
1. `track_creator` - Add creator to watchlist
2. `list_tracked_creators` - Show all tracked creators
3. `get_zora_signals` - Query recent signals
4. `explain_signal` - Breakdown signal scoring
5. `get_coin_market_state` - Current price/liquidity
6. `preview_trade` - Estimate cost with slippage
7. `execute_trade` - Submit trade (gated)
8. `start_wallet_link` - Initiate secure wallet linking
9. `check_wallet_link_status` - Wallet status
10. `get_position_status` - Open positions
11. `get/update_user_preferences` - Remember settings

### 2. Conversation State Management (`app/bot/conversation_store.py`)

**Per-User Persistence**:
- `ConversationSession` table stores: `telegram_user_id` → `openai_thread_id`
- Singleton OpenAI client initialized at startup
- Shared assistant ID across all users
- Session timeout cleanup (default: 30 minutes)

**Example Flow**:
```python
# User sends a message
thread_id, _ = await get_or_create_conversation_session(telegram_user_id=123)
# thread_id is persisted in DB, reused for subsequent messages
```

### 3. Assistant Orchestration (`app/bot/assistant.py`)

**Main Function**: `send_message_to_assistant(telegram_user_id, user_message)`

**Flow**:
```
1. Get/create conversation thread for user
2. Add user message to thread
3. Run assistant (AI decides which tools to call)
4. Poll for completion (max 30 attempts, 1s interval)
5. While status == "requires_action":
   a. Extract tool calls from run object
   b. Execute tools (call real services)
   c. Submit tool results to OpenAI
   d. Run assistant again
   e. Repeat
6. Extract final assistant message
7. Return AssistantResponse with text + metadata
```

**Safety Limits**:
- `max_iterations=5` (prevents infinite loops)
- Poll timeout (30 attempts)
- Proper error handling for all statuses

### 4. Real Tool Execution (`app/bot/tools.py`) - NEW Phase 2

**ToolExecutor Class** (440+ lines):

Maps each tool to backend services:

```python
async def track_creator(self, args) -> dict:
    # Wires to: TrackedCreatorRepository.create()
    repo = TrackedCreatorRepository(self.session)
    tracked = TrackedCreator(...)
    self.session.add(tracked)
    await self.session.commit()
    return {"success": True, "data": {...}}

async def get_zora_signals(self, args) -> dict:
    # Wires to: SignalRepository.get_recent()
    repo = SignalRepository(self.session)
    signals = await repo.get_recent(limit=limit)
    return {"success": True, "data": {...}}

async def preview_trade(self, args) -> dict:
    # Wires to: ZoraCoinRepository + pricing
    coin_repo = ZoraCoinRepository(self.session)
    coin = await coin_repo.get_by_symbol(coin_symbol)
    # Calculate slippage, fees, total cost
    return {"success": True, "data": {...}}
```

**Key Design**:
- All tool results return: `{"success": bool, "data": object} | {"success": False, "error": str}`
- Database transactions properly committed
- Full error logging and exception handling
- No hardcoded secrets (all from config/env)

### 5. Inline Button Callbacks (`app/bot/inline_buttons.py`) - NEW Phase 2

**Interactive Actions**:

After trade preview, user sees buttons:
```
┌──────────────────────────────┐
│ Preview: BUY 0.01 ETH of TEST│
│ Price: $0.0234               │
│ Slippage: 1.5% ($15)         │
│ Fees: 0.3% ($3)              │
│ Total: $118                   │
│                               │
│ [✅ BUY]  [❌ CANCEL]        │
└──────────────────────────────┘
```

**Button Types**:
- **Trade Confirmation** (`trade_confirm|coin=TEST|action=buy|amount=100`)
- **Trade Cancellation** (`trade_cancel|coin=TEST`)
- **Position Close** (`close_position|pos_id=123`)
- **Wallet Linking** (URL button to secure flow)
- **Creator Tracking** (confirmation)

**Callback Routing**:
- Pipe-separated (`|`) → AI trading buttons
- Colon-separated (`:`) → Admin approval buttons
- Backward compatible with existing admin callbacks

### 6. Integration with Existing Systems

**Repositories Used**:
- `TrackedCreatorRepository` - Creator watchlist management
- `SignalRepository` - Signal queries with filtering
- `ZoraCoinRepository` + `CoinMarketSnapshotRepository` - Market data
- `PaperPositionRepository` - Paper trading positions
- `UserPreferences` - Remember user settings

**Services Integrated**:
- `ScoringEngine` - Deterministic + LLM scoring
- `WalletLinkingService` - Secure wallet connection
- `PaperEngine` - Paper trading execution (Phase 3)
- `LiveExecution` - Real trading (Phase 3+)

## Example Conversations

### Example 1: Creator Tracking
```
User: "can you track @vitalik's posts for zora coins?"

AI → tool: track_creator(x_username="vitalik", mode="hybrid")
Tool: Uses TrackedCreatorRepository to add to user's watchlist
AI ← returns: Success message

Bot: "✅ Now tracking @vitalik in hybrid mode. I'll notify you of new Zora coins he posts about."
```

### Example 2: Signal Explanation
```
User: "why did you flag coin #TEST42 as bullish?"

AI → tool: explain_signal(signal_id=42)
Tool: Queries Signal model, extracts scoring breakdown
AI ← returns: Structured explanation

Bot: "📊 Score Breakdown:
     Deterministic: 75/100 (strong volume + liquidity)
     LLM Assessment: 80/100 (positive creator sentiment)
     Final: 77/100 → ALERT
     
     ⚠️ This coin shows strong momentum but watch for volatility."
```

### Example 3: Trade Preview & Execution
```
User: "buy $100 of TEST"

AI → tool: preview_trade(coin_symbol="TEST", action="buy", amount_usd=100)
Tool: Queries coin market data, estimates slippage
AI ← returns: Detailed preview with buttons

Bot: "Preview: BUY $100 of TEST
      Price: $0.0234
      Est. Slippage: 1.5% ($1.50)
      Est. Fees: 0.3% ($0.30)
      Total Cost: $101.80
      
      [✅ BUY]  [❌ CANCEL]"

User clicks: [✅ BUY]

AI → tool: execute_trade(coin_symbol="TEST", action="buy", amount_usd=100)
Tool: Check wallet linked + risk controls, then execute (or reject)
AI ← returns: Success/failure + tx details

Bot: "🎯 Trade executed!
      Position opened: #123
      Size: 4,273 TEST
      Entry: $0.0234
      Max Loss: $20 (stop at $0.019)"
```

### Example 4: Position Management
```
User: "what positions do I have?"

AI → tool: get_position_status()
Tool: Queries PaperPosition for user
AI ← returns: List of open positions

Bot: "📊 Your Positions:
      
      Position #1: TEST (Entry: $0.0234)
      • Size: 4,273 coins
      • Cost: $100
      • Current: $102.34 (+2.3%)
      • PnL: +$2.34
      [Close Position]
      
      Position #2: COIN2 (Entry: $1.50)
      • Size: 67 coins
      • Cost: $100.50
      • Current: $98.00 (-2.5%)
      • PnL: -$2.50
      [Close Position]"
```

## Data Flow: Message → Response

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. User sends message to Telegram                               │
│    Message: "track vitalik"                                     │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. Telegram → webhook/polling → handle_free_text()              │
│    • Extract user_id, message text                              │
│    • Check feature flag (AI enabled)                            │
│    • Show typing indicator                                      │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. send_message_to_assistant(user_id, text)                     │
│    • Get/create conversation thread in DB                       │
│    • Add message to thread                                      │
│    • OpenAI runs assistant                                      │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 4. OpenAI Responses API returns: requires_action                │
│    • Assistant decided: call track_creator() tool               │
│    • Tool call: name="track_creator"                            │
│                 args={"x_username": "vitalik", "mode": "..."}   │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 5. ToolExecutor.execute_tool()                                  │
│    • Execute real tool: track_creator()                         │
│    • Call TrackedCreatorRepository                              │
│    • Commit to database                                         │
│    • Return result JSON                                         │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 6. Submit tool result back to OpenAI                            │
│    • Result: {"success": true, "data": {...}}                   │
│    • Run assistant again                                        │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 7. OpenAI returns: completed                                    │
│    • Final response text                                        │
│    • Extract message from thread                                │
│    • No more tool calls needed                                  │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 8. Return AssistantResponse to handler                          │
│    • response.text → "✅ Now tracking @vitalik..."              │
│    • response.tools_executed → ["track_creator"]                │
│    • response.error → None                                      │
└──────────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────────┐
│ 9. handle_free_text() replies to user                           │
│    • Check if buttons needed (track_creator = no buttons)       │
│    • Send message back via Telegram                             │
│    • Update last_message_at for session timeout                 │
└──────────────────────────────────────────────────────────────────┘
```

## Safety & Guardrails

### Phase 2 Implementation
- ✅ No hardcoded secrets (all from environment)
- ✅ Database transactions properly committed
- ✅ Error handling on all tool outputs
- ✅ Max iteration limit (5) to prevent loops
- ✅ Poll timeout to prevent hanging
- ✅ Per-user async session isolation

### Phase 3 (Planned)
- Wallet linking verification
- Trade size limits
- Daily loss limits
- Slippage validation
- One-tap mode disabled by default
- Trading gated behind wallet link

## Testing

**Unit Tests** (`tests/unit/test_tools.py`):
- ToolExecutor initialization
- Each tool success/error paths
- Repository interactions (mocked)
- Button generation
- Callback routing

**Integration Tests** (existing):
- `/test_approval_workflow.py` - Signal approval flow
- `/test_paper_trading_flow.py` - Trade lifecycle
- `/test_pipeline.py` - Scoring pipeline

## Configuration

**Required Environment Variables** (in `.env`):
```bash
TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_RESPONSES_MODEL=gpt-4o-mini
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://localhost:6379/0
```

**Feature Flag**:
```python
enable_conversational_mode: bool = True
conversation_timeout_minutes: int = 30
```

## What Users Can Do

1. **Track Creators**: `"track @vitalik"`, `"monitor @creator in hybrid mode"`
2. **Query Signals**: `"show me bullish zora signals"`, `"what signals over 75?"`, `"explain signal #42"`
3. **Check Prices**: `"what's the price of TEST coin?"`
4. **Preview Trades**: `"show me a preview of buying $100 TEST"`, `"estimate slippage for selling"`
5. **Manage Positions**: `"what positions do I have?"`, `"close position #1"`, `"show my PnL"`
6. **Manage Preferences**: `"set my risk level to high"`, `"remember I prefer hybrid tracking"`
7. **Get Help**: `"explain how scoring works"`, `"what's a signal?"`, `"guide me through trading"`

## Files Modified/Created

### Phase 2 (Tool Execution)
- ✅ **NEW**: `app/bot/tools.py` (440+ lines) - Real tool execution
- ✅ **MOD**: `app/bot/assistant.py` - Wire real executor, track tools_executed
- ✅ **NEW**: `tests/unit/test_tools.py` (15+ tests)

### Phase 2B (Inline Buttons)
- ✅ **NEW**: `app/bot/inline_buttons.py` (200+ lines) - Button generation & callbacks  
- ✅ **MOD**: `app/bot/handlers/callbacks.py` - Route AI buttons separately
- ✅ **MOD**: `app/bot/handlers/ai_handlers.py` - Attach buttons to responses
- ✅ **MOD**: `app/bot/assistant.py` - Track tools_executed for button logic

## Next Steps (Phase 3)

1. **Wallet Linking Verification** - Complete secure flow
2. **Risk Controls** - Max trade sizes, daily loss limits
3. **Trade Confirmations** - Multi-step confirmation for large trades
4. **Position Closing** - Handle close_position button callbacks
5. **Paper Trading Execution** - Real position creation
6. **Live Trading** - With explicit user enablement
7. **Enhanced Error Recovery** - Better error messages and recovery

---

**Status**: Phase 2 ✅ Complete - Tool execution and buttons wired. Conversational AI ready for testing with mocked databases.
