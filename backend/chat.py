from prompt import CHAT_SYSTEM_PROMPT
from markdown_utils import normalize_llm_markdown

class ChatSession:
    def __init__(self, llm, context: str = "", history: list = None):
        self.llm = llm
        self.context = context
        self.history = history or []

    def _build_messages(self):
        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        if self.context:
            messages.append({"role": "user", "content": f"以下是论文相关内容：\n{self.context}"})
            messages.append({"role": "assistant", "content": "好的，我已了解这篇论文的内容，请问有什么问题？"})
        messages.extend(self.history)
        return messages

    async def send(self, user_message: str, **kwargs) -> str:
        user_turn = {"role": "user", "content": user_message}
        self.history.append(user_turn)
        try:
            reply = await self.llm.chat(self._build_messages(), **kwargs)
            normalized_reply = normalize_llm_markdown(reply)
        except BaseException:
            # Keep a failed attempt out of the next prompt. Otherwise a retry
            # contains an unanswered duplicate user message.
            if self.history and self.history[-1] is user_turn:
                self.history.pop()
            raise
        self.history.append({"role": "assistant", "content": normalized_reply})
        return normalized_reply

    async def send_stream(self, user_message: str, **kwargs):
        async for stream_chunk in self.send_stream_events(user_message, **kwargs):
            if stream_chunk.kind == "content":
                yield stream_chunk.content

    async def send_stream_events(self, user_message: str, **kwargs):
        user_turn = {"role": "user", "content": user_message}
        self.history.append(user_turn)
        chunks = []
        try:
            async for stream_chunk in self.llm.chat_stream_events(self._build_messages(), **kwargs):
                if stream_chunk.kind == "content":
                    chunks.append(stream_chunk.content)
                yield stream_chunk
            normalized_reply = normalize_llm_markdown("".join(chunks))
        except BaseException:
            # Also covers client cancellation, which closes an async generator
            # with GeneratorExit/CancelledError rather than a normal Exception.
            if self.history and self.history[-1] is user_turn:
                self.history.pop()
            raise
        self.history.append({"role": "assistant", "content": normalized_reply})

    def clear(self):
        self.history.clear()
