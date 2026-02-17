from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from app.services.kokoro_service import init_kokoro, generate_audio_stream
import base64

router = APIRouter()

# ----------------------------
# WebSocket TTS Endpoint (Streaming per sentence)
# ----------------------------
@router.websocket("/ws/tts")
async def websocket_tts(websocket: WebSocket):
    await websocket.accept()
    init_kokoro()
    try:
        while True:
            data = await websocket.receive_json()
            text = data.get("text")
            voice = data.get("voice", "af_heart")
            speed = data.get("speed", 1.0)

            if not text:
                await websocket.send_json({"error": "Text is required"})
                continue

            try:
                # Stream chunks for the sentence
                for chunk_bytes in generate_audio_stream(text, voice, speed):
                    chunk_b64 = base64.b64encode(chunk_bytes).decode("utf-8")
                    await websocket.send_json({
                        "text": text,
                        "voice": voice,
                        "audio_chunk": chunk_b64
                    })
                # Signal end of sentence
                await websocket.send_json({"text": text, "voice": voice, "audio_end": True})

            except Exception as e:
                await websocket.send_json({"error": str(e)})

    except WebSocketDisconnect:
        print("Client disconnected")


# ----------------------------
# HTML Test Page for Chapter Streaming
# ----------------------------
@router.get("/tts-test")
async def tts_test():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Kokoro TTS Chapter Streaming Test</title>
    </head>
    <body>
        <h2>Kokoro TTS Chapter Streaming</h2>
        <textarea id="text" placeholder="Enter chapter text here..." rows="8" cols="60"></textarea>
        <br/>
        <select id="voice">
            <option value="af_heart">af_heart</option>
            <option value="af_bella">af_bella</option>
            <option value="af_sky">af_sky</option>
            <option value="am_adam">am_adam</option>
            <option value="am_michael">am_michael</option>
        </select>
        <button onclick="sendChapter()">Speak Chapter</button>
        <br/><br/>
        <audio id="player" controls></audio>
        <pre id="log" style="border:1px solid #ccc;padding:10px;height:150px;overflow:auto;"></pre>

        <script>
            const ws = new WebSocket("ws://" + location.host + "/ws/tts");
            const log = document.getElementById('log');
            const player = document.getElementById('player');

            let audioQueue = [];
            let isPlaying = false;

            function addLog(message){
                log.textContent += message + "\\n";
                log.scrollTop = log.scrollHeight;
            }

            // Play next sentence in queue
            async function playNext() {
                if (audioQueue.length === 0) {
                    isPlaying = false;
                    return;
                }
                isPlaying = true;
                const blob = audioQueue.shift();
                player.src = URL.createObjectURL(blob);
                await player.play();
                player.onended = () => playNext();
            }

            ws.onopen = () => addLog("WebSocket connected");
            ws.onclose = () => addLog("WebSocket closed");
            ws.onerror = (err) => addLog("WebSocket error: " + err);

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);

                if (data.audio_chunk) {
                    const bytes = Uint8Array.from(atob(data.audio_chunk), c => c.charCodeAt(0));
                    audioQueue.push(new Blob([bytes], { type: "audio/wav" }));
                }

                if (data.audio_end) {
                    addLog("Finished sentence: " + data.text);
                    if (!isPlaying) playNext();
                }

                if (data.error) addLog("Error: " + data.error);
            };

            // Split chapter into sentences and send
            function sendChapter() {
                if (ws.readyState !== WebSocket.OPEN) {
                    addLog("WebSocket not connected!");
                    return;
                }

                const text = document.getElementById('text').value.trim();
                const voice = document.getElementById('voice').value;

                if (!text) {
                    addLog("No text provided!");
                    return;
                }

                // Split into sentences using simple regex
                const sentences = text.match(/[^.!?]+[.!?]?/g) || [text];
                addLog("Sending " + sentences.length + " sentences for TTS...");

                sentences.forEach(sentence => {
                    ws.send(JSON.stringify({ text: sentence.trim(), voice: voice, speed: 1.0 }));
                });
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
