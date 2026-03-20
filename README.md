# 🤖 Crypto Bot Paper Trading v6

Bot de paper trading multi-asset para Binance.
Monitora 12 pares com EMA + RSI + Volume + MACD + Calendário Econômico.

## Arquivos

- `binance_bot_paper_v6.py` — Bot principal
- `requirements.txt` — Dependências Python
- `Procfile` — Instrução de execução para Railway

## Deploy no Railway

1. Faça upload destes 3 arquivos no GitHub
2. Conecte o repositório no Railway
3. O bot sobe automaticamente

## Configuração obrigatória

No Railway, vá em **Variables** e adicione:

```
FINNHUB_API_KEY = sua_chave_finnhub
```

Chave gratuita em: https://finnhub.io

## Monitoramento

Ver logs em tempo real:
Railway → seu projeto → **Deployments → View Logs**
