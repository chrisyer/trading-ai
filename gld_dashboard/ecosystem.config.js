// PM2 Ecosystem — GLD Strangle Remote Operations
// Deploy: pm2 start gld_dashboard/ecosystem.config.js
// Monitor: pm2 monit

module.exports = {
  apps: [
    {
      name: "gld-strangle-dashboard",
      script: "gld_forecast_server.py",
      cwd: "/home/jbot/trading_ai/gld_dashboard",
      interpreter: "python3",
      env: {
        GLD_DASHBOARD_PORT: "8088",
        OLLAMA_URL: "http://localhost:11434",
        OLLAMA_MODEL: "dolphin3",
        DESKTOP_SIGNAL_URL: "http://100.119.161.65:8098/signal/gld",
      },
      max_restarts: 20,
      restart_delay: 5000,
      autorestart: true,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: "gld-signal-poller",
      script: "signal_poller.py",
      cwd: "/home/jbot/trading_ai/gld_dashboard",
      interpreter: "python3",
      env: {
        DESKTOP_SIGNAL_URL: "http://100.119.161.65:8098/signal/gld",
        POLL_INTERVAL: "30",
      },
      max_restarts: 50,
      restart_delay: 10000,
      autorestart: true,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: "gld-alert-service",
      script: "alert_service.py",
      cwd: "/home/jbot/trading_ai/gld_dashboard",
      interpreter: "python3",
      env: {
        GLD_DASHBOARD_URL: "http://localhost:8088",
        DESKTOP_SIGNAL_URL: "http://100.119.161.65:8098",
        // Set these for Telegram alerts:
        // TELEGRAM_BOT_TOKEN: "your_bot_token",
        // TELEGRAM_CHAT_ID: "your_chat_id",
      },
      max_restarts: 50,
      restart_delay: 10000,
      autorestart: true,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: "gld-rh-monitor",
      script: "gld_options_executor.py",
      cwd: "/home/jbot/trading_ai/robinhood_bridge",
      interpreter: "python3",
      env: {
        // Set these for Robinhood:
        // ROBINHOOD_USER: "your_email",
        // ROBINHOOD_PASS: "your_password",
        GLD_DASHBOARD_URL: "http://localhost:8088",
      },
      max_restarts: 20,
      restart_delay: 30000,
      autorestart: true,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
  ],
};
