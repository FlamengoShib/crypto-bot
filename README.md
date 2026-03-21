# 🤖 Crypto Bot v7.1 — Binance Futures

Bot de paper trading automatizado para Binance Futures com suporte a Long e Short, análise multi-timeframe e gestão de risco integrada.

---

## 📋 Funcionalidades

- **Exchange:** Binance Futures (USDT Perpetual)
- **Direções:** Long ↗️ e Short ↘️
- **Pares:** Dinâmicos — todos os pares com volume > 5M USDT/24h (80~120 pares)
- **Timeframe:** 1h principal + 15m confirmação (multi-timeframe)
- **Indicadores:** EMA 12/26 + RSI + Volume + MACD
- **Trailing Stop:** Ativa automaticamente após +1.5% de lucro
- **Calendário econômico:** Pausa automática em eventos de alto impacto
- **Notificações:** Telegram em tempo real
- **Logs:** JSON estruturado + console

---

## ⚙️ Configuração

### 1. Variáveis de ambiente

Configure as seguintes variáveis na sua plataforma (Railway, Heroku, etc.). **Nunca coloque tokens diretamente no código.**

| Variável | Obrigatória | Descrição |
|---|---|---|
| `TELEGRAM_TOKEN` | ✅ Sim | Token do seu bot Telegram |
| `TELEGRAM_CHAT_ID` | ✅ Sim | ID do chat para receber notificações |
| `FINNHUB_API_KEY` | ❌ Opcional | Calendário econômico em tempo real |

### 2. Como obter o Telegram Token

1. Abra o Telegram e procure por `@BotFather`
2. Envie `/newbot` e siga as instruções
3. Copie o token gerado e adicione como `TELEGRAM_TOKEN`
4. Para o `TELEGRAM_CHAT_ID`, envie uma mensagem pro seu bot e acesse:
   `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates`

---

## 🚀 Deploy no Railway

1. Faça o fork ou upload deste repositório no GitHub
2. No Railway, crie um novo projeto a partir do repositório
3. Vá em **Variables** e adicione as variáveis de ambiente acima
4. O deploy acontece automaticamente a cada push no GitHub
5. Acompanhe os logs em **Deployments → Logs**

### Confirmação de que subiu corretamente

Procure essa linha no início dos logs:
```
🤖 CRYPTO BOT BINANCE FUTURES v7.1 — LONG + SHORT
```

---

## 📊 Parâmetros de trading

| Parâmetro | Valor | Descrição |
|---|---|---|
| `SALDO_INICIAL` | 100 USDT | Saldo simulado inicial |
| `TRADE_PCT` | 95% | Percentual do saldo por operação |
| `STOP_LOSS_PCT` | 1.5% | Stop loss por operação |
| `STOP_GAIN_PCT` | 3.0% | Stop gain por operação (R:R 2:1) |
| `TRAILING_PCT` | 1.5% | Ativa trailing stop após este lucro |
| `DAILY_GAIN` | 4.0% | Meta diária — bot pausa ao atingir |
| `DAILY_LOSS` | 2.5% | Limite diário — bot pausa ao atingir |
| `TIMEFRAME_PRINCIPAL` | 1h | Timeframe para geração de sinais |
| `TIMEFRAME_CONFIRM` | 15m | Timeframe de confirmação |
| `VOLUME_MINIMO_24H` | 5M USDT | Volume mínimo para incluir par |

---

## 📈 Lógica de sinais

```
LONG  = EMA_FAST > EMA_SLOW  +  pelo menos 1 de: RSI < 55, Volume > 1.1x média, MACD positivo
SHORT = EMA_FAST < EMA_SLOW  +  pelo menos 1 de: RSI > 50, Volume > 1.1x média, MACD negativo
```

Após gerar sinal no 1h, o bot confirma no 15m antes de abrir posição. Se o 15m não confirmar, aguarda o próximo ciclo.

---

## 📁 Arquivos

| Arquivo | Descrição |
|---|---|
| `crypto_bot_v7.1.py` | Código principal do bot |
| `Procfile` | Instrução de execução para o Railway/Heroku |
| `requirements.txt` | Dependências Python |
| `bot_log.jsonl` | Log estruturado de operações (gerado em runtime) |
| `bot_console.log` | Log de console (gerado em runtime) |

---

## 📦 Dependências

```
ccxt>=4.2.0
pandas>=1.5.0
ta>=0.10.0
requests>=2.28.0
numpy>=1.23.0
```

---

## ⚠️ Aviso

Este bot opera em modo **paper trading** (simulado). Nenhuma ordem real é enviada à exchange. Antes de operar com dinheiro real, valide a estratégia com dados históricos e entenda os riscos envolvidos. Trading de criptomoedas envolve risco de perda total do capital investido.

---

## 🔄 Histórico de versões

| Versão | Principais mudanças |
|---|---|
| v7.1 | Migração Bybit → Binance Futures, pares dinâmicos com filtro de volume |
| v7.0 | RSI/Volume ajustados, multi-timeframe, trailing stop, metas realistas |
| v6.3 | Versão original com lista fixa de 11 pares na Bybit |
