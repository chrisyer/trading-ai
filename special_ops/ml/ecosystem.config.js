module.exports = {
  apps: [
    {
      name: "ml-direction-api",
      cwd: "/home/jbot/trading_ai/special_ops",
      script: "/home/jbot/.local/bin/uvicorn",
      args: "ml.serving.direction_api:app --host 0.0.0.0 --port 8040",
      interpreter: "none",
      env: {
        CUDA_VISIBLE_DEVICES: "0",
      },
      max_restarts: 10,
      restart_delay: 5000,
      autorestart: true,
    },
    {
      name: "ml-sizing-api",
      cwd: "/home/jbot/trading_ai/special_ops",
      script: "/home/jbot/.local/bin/uvicorn",
      args: "ml.serving.sizing_api:app --host 0.0.0.0 --port 8041",
      interpreter: "none",
      env: {
        CUDA_VISIBLE_DEVICES: "0",
      },
      max_restarts: 10,
      restart_delay: 5000,
      autorestart: true,
    },
    {
      name: "ml-governor-api",
      cwd: "/home/jbot/trading_ai/special_ops",
      script: "/home/jbot/.local/bin/uvicorn",
      args: "ml.serving.governor_api:app --host 0.0.0.0 --port 8043",
      interpreter: "none",
      env: {
        CUDA_VISIBLE_DEVICES: "0",
        OLLAMA_URL: "http://localhost:11434",
        OLLAMA_MODEL: "qwen2.5:7b-instruct",
      },
      max_restarts: 10,
      restart_delay: 10000,
      autorestart: true,
    },
    {
      name: "ml-data-ingest",
      cwd: "/home/jbot/trading_ai/special_ops",
      script: "ml/data/ingest.py",
      interpreter: "python3",
      env: {
        CRELLA_IP: "100.119.161.65",
        CRELLA_PORT: "8097",
        INGEST_POLL_SECONDS: "60",
      },
      max_restarts: 10,
      restart_delay: 10000,
      autorestart: true,
    },
  ],
};
