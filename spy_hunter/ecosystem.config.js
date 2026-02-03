module.exports = {
  apps: [
    {
      name: 'spy-hunter-api',
      script: 'spy_signal_api.py',
      interpreter: 'python3',
      cwd: '/home/jbot/trading_ai/spy_hunter',
      env: {
        PYTHONPATH: '/home/jbot/trading_ai'
      },
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '500M',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z'
    },
    {
      name: 'spy-hunter-trainer',
      script: 'continuous_trainer.py',
      interpreter: 'python3',
      cwd: '/home/jbot/trading_ai/spy_hunter',
      env: {
        PYTHONPATH: '/home/jbot/trading_ai',
        CUDA_VISIBLE_DEVICES: '0'
      },
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '8G',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z'
    }
  ]
};
