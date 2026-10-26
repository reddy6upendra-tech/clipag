import os
import uvicorn
import gradio as gr
from app.api.server import app

# Minimal Gradio interface so Hugging Face Space's Gradio SDK detects the app
with gr.Blocks(title="AI Reels Clipper") as demo:
    gr.Markdown("# 🎬 AI Reels Clipper")
    gr.Markdown("Web dashboard active. [Click here to open dashboard](/)")
    gr.HTML('<a href="/" target="_self" style="display:inline-block;padding:10px 20px;background:#6366f1;color:white;border-radius:8px;text-decoration:none;font-weight:bold;">Open Dashboard &rarr;</a>')

# Mount Gradio at /gradio, keeping root "/" for our full interactive web dashboard
app = gr.mount_gradio_app(app, demo, path="/gradio")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
