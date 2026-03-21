"""
╔══════════════════════════════════════════════════════════════╗
║        SENAC CRYPTO BOT — PAPER TRADING v7.1                 ║
║   Exchange: BINANCE FUTURES (maior volume do mundo)          ║
║   EMA 12/26 + RSI + Volume + MACD — Multi-Timeframe 1h+15m  ║
║   Stop Loss: 1.5% | Stop Gain: 3%                            ║
║   Meta dia: +4% | Limite: -2.5%                              ║
║   Pares: todos USDT Futures c/ volume > 5M USDT/24h          ║
║   Calendário Econômico + Pausa automática                     ║
╚══════════════════════════════════════════════════════════════╝

MELHORIAS v7.1 vs v7.0:
  - Exchange: Bybit → Binance Futures (maior volume, menor spread)
  - Pares: lista fixa → dinâmica (todos USDT Futures ativos)
  - Filtro de volume mínimo: 5M USDT/24h (garante liquidez)
  - Pares recarregados a cada 24h automaticamente
"""

import os
import time
import logging
import json
import requests
from datetime import datetime, timedelta
import ccxt
import pandas as pd
import ta
import numpy as np

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES — todas via variável de ambiente
# ─────────────────────────────────────────────

FINNHUB_API_KEY  = os.getenv("FINNHUB_API_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",  "")   # nunca hardcode aqui!
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","")

SYMBOLS = []  # preenchido dinamicamente na conexão
VOLUME_MINIMO_24H = 5_000_000  # 5 milhões USDT/24h — garante liquidez

SALDO_INICIAL  = 100.0
TRADE_PCT      = 0.95     # usa 95% do saldo por operação (5% de reserva)
MAX_POSICOES   = 1
STOP_LOSS_PCT  = 1.5      # era 1.0 — stop menos apertado
STOP_GAIN_PCT  = 3.0      # era 2.0 — R:R de 2:1
TRAILING_PCT   = 1.5      # ativa trailing stop após 1.5% de lucro
TIMEFRAME_PRINCIPAL = '1h'
TIMEFRAME_CONFIRM   = '15m'
CHECK_EVERY    = 120      # verifica a cada 2 minutos (era 60s)

# Indicadores — ajustados para operar mais
EMA_FAST   = 12           # era 9
EMA_SLOW   = 26           # era 21
RSI_PERIOD = 14
RSI_BUY    = 55           # era 45 — muito restritivo
RSI_SELL   = 50           # era 60 — contraditório com EMA de baixa
VOL_FACTOR = 1.1          # era 1.3 — 10% acima da média é suficiente

# Meta diária — realista
DAILY_LOSS  = 2.5         # era 5.0 — stop mais conservador
DAILY_GAIN  = 4.0         # era 12.0 — meta atingível (2 ops boas/dia)

PAUSA_ANTES  = 30
PAUSA_DEPOIS = 20         # era 30 — menos tempo parado pós-evento

EVENTOS_CRIPTO = [
    "fed", "federal reserve", "fomc", "interest rate",
    "cpi", "inflation", "nfp", "non-farm", "payroll",
    "gdp", "unemployment", "powell", "pce", "ppi",
    "retail sales", "sec", "bitcoin", "crypto", "jobs"
]

LOG_FILE = "bot_log.jsonl"

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot_console.log")]
)
log = logging.getLogger("CryptoBot")


def log_ev(event: dict):
    event["timestamp"] = datetime.utcnow().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def telegram(mensagem: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("⚠️  Telegram não configurado (defina TELEGRAM_TOKEN e TELEGRAM_CHAT_ID)")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensagem,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.warning(f"Telegram erro: {e}")


# ─────────────────────────────────────────────
#  CONEXÃO — BINANCE FUTURES
# ─────────────────────────────────────────────

_ultimo_reload_symbols = 0  # controla recarga diária dos pares


def carregar_symbols(exchange) -> list:
    """
    Busca todos os pares USDT Perpetual ativos na Binance Futures
    e filtra pelos que têm volume mínimo de 24h configurado.
    """
    global _ultimo_reload_symbols
    log.info("🔄 Carregando pares da Binance Futures...")
    mercados = exchange.load_markets(reload=True)

    # Todos os pares USDT perpetual ativos
    candidatos = [
        info['symbol']                          # ex: BTC/USDT
        for m, info in mercados.items()
        if '/USDT' in m
        and info.get('active', False)
        and info.get('type') == 'swap'
        and info.get('linear', False)           # só USDT-margined (não coin-margined)
    ]

    # Filtrar por volume mínimo de 24h
    log.info(f"   Verificando volume de {len(candidatos)} candidatos...")
    validos = []
    for sym in candidatos:
        try:
            tk = exchange.fetch_ticker(sym)
            vol24h = float(tk.get('quoteVolume') or 0)
            if vol24h >= VOLUME_MINIMO_24H:
                validos.append(sym)
            time.sleep(0.08)
        except Exception:
            continue

    validos.sort()
    _ultimo_reload_symbols = time.time()
    log.info(f"✅ {len(validos)} pares com volume >{VOLUME_MINIMO_24H/1e6:.0f}M USDT/24h")
    return validos


def conectar():
    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
    })
    ticker = exchange.fetch_ticker('BTC/USDT')
    log.info(f"✅ Conectado Binance Futures — BTC: {ticker['last']:.2f} USDT")

    global SYMBOLS
    SYMBOLS = carregar_symbols(exchange)
    telegram(
        f"🔗 <b>Binance Futures conectada!</b>\n"
        f"📊 {len(SYMBOLS)} pares ativos (vol >{VOLUME_MINIMO_24H/1e6:.0f}M USDT/24h)\n"
        f"🔄 Lista recarregada a cada 24h automaticamente"
    )
    return exchange


def recarregar_symbols_se_necessario(exchange):
    """Recarrega a lista de pares uma vez por dia."""
    global SYMBOLS, _ultimo_reload_symbols
    if time.time() - _ultimo_reload_symbols > 86400:  # 24h
        log.info("🔄 Recarga diária dos pares...")
        SYMBOLS = carregar_symbols(exchange)


def obter_candles(exchange, symbol, timeframe='1h', limit=150) -> pd.DataFrame:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    for col in ['close','volume','high','low','open']:
        df[col] = df[col].astype(float)
    return df


def preco_atual(exchange, symbol) -> float:
    return float(exchange.fetch_ticker(symbol)['last'])


# ─────────────────────────────────────────────
#  INDICADORES
# ─────────────────────────────────────────────

def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"]   = ta.trend.EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema_slow"]   = ta.trend.EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi"]        = ta.momentum.RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    df["vol_ma"]     = df["volume"].rolling(20).mean()
    macd             = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
    df["macd_linha"] = macd.macd()
    df["macd_sinal"] = macd.macd_signal()
    df["macd_hist"]  = macd.macd_diff()
    return df.dropna()


def avaliar_sinal(df: pd.DataFrame) -> tuple:
    """
    Lógica v7: EMA obrigatória + pelo menos 1 dos outros 3 (RSI, Volume, MACD).
    Mais permissivo que v6 (era 2/3), mas ainda filtra entradas ruins.
    Retorna (sinal, conf, motivos_nao_entrada)
    """
    if len(df) < 2:
        return "HOLD", {}, ["dados insuficientes"]

    last = df.iloc[-1]
    prev = df.iloc[-2]

    ema_alta  = last["ema_fast"] > last["ema_slow"]
    ema_baixa = last["ema_fast"] < last["ema_slow"]

    # RSI com threshold ajustado
    rsi_buy  = last["rsi"] < RSI_BUY   # < 55 (era < 45)
    rsi_sell = last["rsi"] > RSI_SELL  # > 50 (era > 60)

    # Volume levemente acima da média
    vol_ok = last["volume"] > last["vol_ma"] * VOL_FACTOR

    # MACD — cruzamento ou histograma positivo/negativo
    macd_buy  = last["macd_hist"] > 0
    macd_sell = last["macd_hist"] < 0

    # NOVO: checar cruzamento EMA recente (mais força no sinal)
    ema_cruzou_alta  = (prev["ema_fast"] <= prev["ema_slow"]) and ema_alta
    ema_cruzou_baixa = (prev["ema_fast"] >= prev["ema_slow"]) and ema_baixa

    outros_buy  = sum([rsi_buy,  vol_ok, macd_buy ])
    outros_sell = sum([rsi_sell, vol_ok, macd_sell])

    buy_score  = sum([ema_alta,  rsi_buy,  vol_ok, macd_buy ])
    sell_score = sum([ema_baixa, rsi_sell, vol_ok, macd_sell])

    # Bônus de score se EMA cruzou recentemente
    bonus_buy  = 0.5 if ema_cruzou_alta  else 0
    bonus_sell = 0.5 if ema_cruzou_baixa else 0

    conf = {
        "ema":         "ALTA" if ema_alta else "BAIXA",
        "ema_cruzou":  ema_cruzou_alta or ema_cruzou_baixa,
        "rsi":         round(float(last["rsi"]), 1),
        "vol_ratio":   round(float(last["volume"]) / float(last["vol_ma"]), 2) if last["vol_ma"] > 0 else 0,
        "macd_hist":   round(float(last["macd_hist"]), 6),
        "buy_score":   round(buy_score + bonus_buy, 1),
        "sell_score":  round(sell_score + bonus_sell, 1),
    }

    motivos = []

    # LONG: EMA alta + pelo menos 1 dos outros 3
    if ema_alta and outros_buy >= 1:
        return "LONG", conf, []

    # SHORT: EMA baixa + pelo menos 1 dos outros 3
    if ema_baixa and outros_sell >= 1:
        return "SHORT", conf, []

    # Registrar por que não entrou (diagnóstico)
    if not ema_alta and not ema_baixa:
        motivos.append("EMA neutro (cruzamento recente)")
    if ema_alta and outros_buy == 0:
        motivos.append(f"EMA alta mas RSI={conf['rsi']} vol={conf['vol_ratio']}x MACD={'pos' if macd_buy else 'neg'} — nenhum confirmou")
    if ema_baixa and outros_sell == 0:
        motivos.append(f"EMA baixa mas RSI={conf['rsi']} vol={conf['vol_ratio']}x MACD={'neg' if macd_sell else 'pos'} — nenhum confirmou")

    return "HOLD", conf, motivos


def confirmar_timeframe_menor(exchange, symbol) -> str:
    """
    Confirmação no 15min: verifica se a direção de curto prazo bate com o 1h.
    Retorna 'LONG', 'SHORT' ou 'NEUTRO'.
    """
    try:
        df15 = calcular_indicadores(obter_candles(exchange, symbol, TIMEFRAME_CONFIRM, limit=60))
        if len(df15) < 2:
            return "NEUTRO"
        last = df15.iloc[-1]
        if last["ema_fast"] > last["ema_slow"] and last["macd_hist"] > 0:
            return "LONG"
        if last["ema_fast"] < last["ema_slow"] and last["macd_hist"] < 0:
            return "SHORT"
        return "NEUTRO"
    except Exception as e:
        log.warning(f"Confirmação 15m {symbol}: {e}")
        return "NEUTRO"


# ─────────────────────────────────────────────
#  CALENDÁRIO ECONÔMICO
# ─────────────────────────────────────────────

class CalendarioEconomico:
    def __init__(self):
        self.eventos_hoje = []
        self.ultimo_fetch = None
        self._carregar_eventos()

    def _carregar_eventos(self):
        if not FINNHUB_API_KEY:
            self._eventos_fixos()
            return
        try:
            hoje = datetime.now().strftime("%Y-%m-%d")
            resp = requests.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={"from": hoje, "to": hoje, "token": FINNHUB_API_KEY},
                timeout=10
            )
            if resp.status_code == 200:
                eventos = resp.json().get("economicCalendar", [])
                self.eventos_hoje = [
                    {"nome": e.get("event"), "hora": e.get("time",""),
                     "impact": e.get("impact","")}
                    for e in eventos
                    if e.get("impact","").lower() in ("high",)  # só high impact
                    and any(k in (e.get("event") or "").lower() for k in EVENTOS_CRIPTO)
                ]
                self.ultimo_fetch = time.time()
                log.info(f"📅 {len(self.eventos_hoje)} eventos de alto impacto hoje")
                for ev in self.eventos_hoje:
                    log.info(f"   ⏰ {ev['hora']} — {ev['nome']}")
            else:
                self._eventos_fixos()
        except Exception as e:
            log.warning(f"⚠️  Calendário: {e}")
            self._eventos_fixos()

    def _eventos_fixos(self):
        """Eventos fixos conservadores — só os de maior impacto comprovado."""
        dia = datetime.now().weekday()
        self.eventos_hoje = []
        if dia == 4:  # sexta
            self.eventos_hoje.append({"nome":"Non-Farm Payroll","hora":"08:30","impact":"high"})
        # FOMC só em meses específicos — aqui mantemos como placeholder
        log.info(f"📅 Eventos fixos: {len(self.eventos_hoje)}")

    def verificar_atualizacao(self):
        if self.ultimo_fetch and (time.time() - self.ultimo_fetch) > 3600:
            self._carregar_eventos()

    def em_zona_de_risco(self) -> tuple:
        agora = datetime.now()
        for ev in self.eventos_hoje:
            try:
                partes  = ev.get("hora","").split(":")
                hora_ev = agora.replace(hour=int(partes[0]),
                    minute=int(partes[1]) if len(partes) > 1 else 0,
                    second=0, microsecond=0)
                inicio = hora_ev - timedelta(minutes=PAUSA_ANTES)
                fim    = hora_ev + timedelta(minutes=PAUSA_DEPOIS)
                if inicio <= agora <= fim:
                    falta = int((hora_ev - agora).total_seconds()/60) if agora < hora_ev else 0
                    msg = f"{ev['nome']} em {falta}min" if falta > 0 else f"Pós-evento {ev['nome']}"
                    return True, msg
            except Exception:
                continue
        return False, ""

    def proximo_evento(self) -> str:
        agora = datetime.now()
        proximos = []
        for ev in self.eventos_hoje:
            try:
                partes  = ev.get("hora","").split(":")
                hora_ev = agora.replace(hour=int(partes[0]),
                    minute=int(partes[1]) if len(partes) > 1 else 0,
                    second=0, microsecond=0)
                if hora_ev > agora:
                    falta = int((hora_ev - agora).total_seconds()/60)
                    proximos.append((falta, f"{ev['nome']} às {ev['hora']} (em {falta}min)"))
            except Exception:
                continue
        proximos.sort()
        return proximos[0][1] if proximos else "Nenhum evento relevante hoje"


# ─────────────────────────────────────────────
#  GESTOR DIÁRIO
# ─────────────────────────────────────────────

class GestorDiario:
    def __init__(self, saldo_ini):
        self.saldo_inicio_dia = saldo_ini
        self.data_atual       = datetime.now().date()
        self.bloqueado        = False
        self.motivo           = ""

    def verificar_reset(self, saldo):
        hoje = datetime.now().date()
        if hoje > self.data_atual:
            log.info(f"🌅 Novo dia — meta resetada!")
            self.saldo_inicio_dia = saldo
            self.data_atual       = hoje
            self.bloqueado        = False
            self.motivo           = ""
            telegram(
                f"🌅 <b>Novo dia iniciado!</b>\n"
                f"Saldo de referência: {saldo:.2f} USDT\n"
                f"Meta: +{DAILY_GAIN}% | Limite: -{DAILY_LOSS}%"
            )

    def verificar_limites(self, saldo) -> bool:
        if self.bloqueado:
            return True
        pnl = ((saldo - self.saldo_inicio_dia) / self.saldo_inicio_dia) * 100
        if pnl <= -DAILY_LOSS:
            self.bloqueado = True
            self.motivo    = f"LOSS -{DAILY_LOSS}%"
            log.warning(f"🛑 LIMITE LOSS DIÁRIO: {pnl:.2f}%")
            log_ev({"type":"DAILY_BLOCKED","motivo":"LOSS","pnl":round(pnl,2)})
            telegram(
                f"🛑 <b>LIMITE DIÁRIO DE LOSS ATINGIDO</b>\n"
                f"P&L do dia: {pnl:.2f}%\nBot pausado até amanhã."
            )
            return True
        if pnl >= DAILY_GAIN:
            self.bloqueado = True
            self.motivo    = f"GAIN +{DAILY_GAIN}%"
            log.info(f"🎯 META DIÁRIA ATINGIDA: +{pnl:.2f}%")
            log_ev({"type":"DAILY_BLOCKED","motivo":"GAIN","pnl":round(pnl,2)})
            telegram(
                f"🎯 <b>META DIÁRIA ATINGIDA!</b>\n"
                f"P&L do dia: +{pnl:.2f}%\nBot pausado até amanhã. 🏆"
            )
            return True
        return False

    def status(self, saldo):
        pnl = ((saldo - self.saldo_inicio_dia) / self.saldo_inicio_dia) * 100
        return f"P&L hoje: {pnl:+.2f}% | Meta:+{DAILY_GAIN}% | Limite:-{DAILY_LOSS}%"


# ─────────────────────────────────────────────
#  POSIÇÃO — LONG E SHORT COM TRAILING STOP
# ─────────────────────────────────────────────

class Posicao:
    def __init__(self, symbol, preco, usdt, direcao='LONG'):
        self.symbol        = symbol
        self.direcao       = direcao
        self.preco_entrada = preco
        self.qty           = usdt / preco
        self.usdt_alocado  = usdt
        self.trailing_ativo = False
        self.trailing_preco = None  # melhor preço atingido

        if direcao == 'LONG':
            self.stop_loss = preco * (1 - STOP_LOSS_PCT / 100)
            self.stop_gain = preco * (1 + STOP_GAIN_PCT / 100)
        else:
            self.stop_loss = preco * (1 + STOP_LOSS_PCT / 100)
            self.stop_gain = preco * (1 - STOP_GAIN_PCT / 100)

    def atualizar_trailing(self, preco):
        """Ativa trailing stop após TRAILING_PCT de lucro."""
        pnl = self.pnl_pct(preco)
        if pnl >= TRAILING_PCT and not self.trailing_ativo:
            self.trailing_ativo = True
            self.trailing_preco = preco
            log.info(f"   🔄 Trailing stop ativado em {preco:.4f} ({pnl:+.2f}%)")

        if self.trailing_ativo:
            if self.direcao == 'LONG':
                if preco > self.trailing_preco:
                    self.trailing_preco = preco
                    # Move stop loss para 1% abaixo do melhor preço
                    novo_sl = preco * (1 - STOP_LOSS_PCT / 100)
                    if novo_sl > self.stop_loss:
                        self.stop_loss = novo_sl
            else:
                if preco < self.trailing_preco:
                    self.trailing_preco = preco
                    novo_sl = preco * (1 + STOP_LOSS_PCT / 100)
                    if novo_sl < self.stop_loss:
                        self.stop_loss = novo_sl

    def verificar_stops(self, preco):
        self.atualizar_trailing(preco)
        if self.direcao == 'LONG':
            if preco <= self.stop_loss: return "STOP_LOSS"
            if preco >= self.stop_gain: return "STOP_GAIN"
        else:
            if preco >= self.stop_loss: return "STOP_LOSS"
            if preco <= self.stop_gain: return "STOP_GAIN"
        return "OK"

    def pnl_pct(self, preco):
        if self.direcao == 'LONG':
            return ((preco - self.preco_entrada) / self.preco_entrada) * 100
        else:
            return ((self.preco_entrada - preco) / self.preco_entrada) * 100


# ─────────────────────────────────────────────
#  CARTEIRA MULTI-ASSET
# ─────────────────────────────────────────────

class CarteiraMulti:
    def __init__(self, saldo_ini):
        self.saldo     = saldo_ini
        self.saldo_ini = saldo_ini
        self.posicoes  = {}
        self.trades    = []
        log.info(f"💼 Carteira Futures 1x: {saldo_ini:.2f} USDT | {len(SYMBOLS)} pares")
        log_ev({"type":"PAPER_START","saldo":saldo_ini,"symbols":SYMBOLS,
                "sl":STOP_LOSS_PCT,"sg":STOP_GAIN_PCT,"modo":"FUTURES_1X_v7"})

    def total(self, precos):
        return self.saldo + sum(
            pos.usdt_alocado * (1 + pos.pnl_pct(precos.get(s, pos.preco_entrada))/100)
            for s, pos in self.posicoes.items()
        )

    def abrir(self, symbol, preco, conf, direcao='LONG'):
        if symbol in self.posicoes or len(self.posicoes) >= MAX_POSICOES:
            return
        usdt = self.saldo * TRADE_PCT
        if usdt < 5:
            log.warning(f"⚠️  Saldo insuficiente: {self.saldo:.2f} USDT")
            return
        self.saldo -= usdt
        pos = Posicao(symbol, preco, usdt, direcao)
        self.posicoes[symbol] = pos
        emoji = "🟢" if direcao == 'LONG' else "🔴"
        tipo  = "LONG ↗️" if direcao == 'LONG' else "SHORT ↘️"
        trail = f" | Trailing após +{TRAILING_PCT}%"
        log.info(f"{emoji} {tipo} {symbol} @ {preco:.4f} | SL:{pos.stop_loss:.4f} | SG:{pos.stop_gain:.4f}{trail}")
        log_ev({"type":f"OPEN_{direcao}","symbol":symbol,"price":preco,
                "usdt":round(usdt,2),"direcao":direcao,
                "sl":round(pos.stop_loss,4),"sg":round(pos.stop_gain,4)})
        telegram(
            f"{emoji} <b>{tipo} — {symbol}</b>\n"
            f"Entrada: <b>{preco:.4f}</b> USDT\n"
            f"Stop Loss: {pos.stop_loss:.4f} USDT (-{STOP_LOSS_PCT}%)\n"
            f"Stop Gain: {pos.stop_gain:.4f} USDT (+{STOP_GAIN_PCT}%)\n"
            f"Trailing: ativa após +{TRAILING_PCT}%\n"
            f"Alocado: {usdt:.2f} USDT\n"
            f"Score: L{conf.get('buy_score',0)}/4 S{conf.get('sell_score',0)}/4\n"
            f"─────────────────\n"
            f"💰 Saldo caixa: {self.saldo:.2f} USDT"
        )

    def fechar(self, symbol, preco, motivo, conf={}):
        if symbol not in self.posicoes:
            return
        pos      = self.posicoes[symbol]
        pnl      = pos.pnl_pct(preco)
        pnl_usdt = pos.usdt_alocado * (pnl / 100)
        self.saldo += pos.usdt_alocado + pnl_usdt
        self.trades.append({"symbol":symbol,"pnl_pct":pnl,
                             "motivo":motivo,"direcao":pos.direcao})
        emoji = "🎯" if motivo=="STOP_GAIN" else "🛑" if motivo=="STOP_LOSS" else "📅" if motivo=="CALENDARIO" else "🔄" if motivo=="TRAILING" else "🔀"
        log.info(f"{emoji} FECHA {pos.direcao} {symbol} ({motivo}) @ {preco:.4f} | P&L:{pnl:+.2f}%")
        log_ev({"type":motivo if "STOP" in motivo else "CLOSE","symbol":symbol,
                "price":preco,"direcao":pos.direcao,
                "pnl_pct":round(pnl,2),"pnl_usdt":round(pnl_usdt,2)})
        telegram(
            f"{emoji} <b>{motivo} — {symbol} ({pos.direcao})</b>\n"
            f"Saída: <b>{preco:.4f}</b> USDT\n"
            f"P&L: <b>{pnl:+.2f}%</b> ({pnl_usdt:+.2f} USDT)\n"
            f"─────────────────\n"
            f"💰 Saldo: {self.saldo:.2f} USDT"
        )
        del self.posicoes[symbol]

    def fechar_todas(self, precos, motivo="CALENDARIO"):
        for sym in list(self.posicoes.keys()):
            self.fechar(sym, precos.get(sym, self.posicoes[sym].preco_entrada), motivo)

    def resumo(self, precos):
        tot  = self.total(precos)
        pnl  = ((tot - self.saldo_ini) / self.saldo_ini) * 100
        wins = sum(1 for t in self.trades if t["pnl_pct"] > 0)
        wr   = wins / len(self.trades) * 100 if self.trades else 0
        log.info("="*60)
        log.info(f"  Saldo final: {tot:.2f} USDT | P&L: {pnl:+.2f}% | WR: {wr:.0f}%")
        por_par = {}
        for t in self.trades:
            s = t["symbol"]
            if s not in por_par:
                por_par[s] = {"trades":0,"pnl":0,"wins":0}
            por_par[s]["trades"] += 1
            por_par[s]["pnl"]    += t["pnl_pct"]
            if t["pnl_pct"] > 0:
                por_par[s]["wins"] += 1
        for sym, info in sorted(por_par.items(), key=lambda x: -x[1]["pnl"]):
            wr_sym = info["wins"] / info["trades"] * 100 if info["trades"] > 0 else 0
            log.info(f"  {sym:12} | {info['trades']} trades | WR:{wr_sym:.0f}% | P&L:{info['pnl']:+.2f}%")
        log.info("="*60)
        log_ev({"type":"SUMMARY","saldo_final":round(tot,2),
                "pnl":round(pnl,2),"wr":round(wr,1),"total_trades":len(self.trades)})


# ─────────────────────────────────────────────
#  LOOP PRINCIPAL
# ─────────────────────────────────────────────

def main():
    log.info("="*60)
    log.info("  🤖 CRYPTO BOT BINANCE FUTURES v7.1 — LONG + SHORT")
    log.info(f"  Pares: dinâmicos (vol >{VOLUME_MINIMO_24H/1e6:.0f}M USDT/24h)")
    log.info(f"  SL:-{STOP_LOSS_PCT}% | SG:+{STOP_GAIN_PCT}% | Trailing:+{TRAILING_PCT}%")
    log.info(f"  Meta:+{DAILY_GAIN}% | Limite:-{DAILY_LOSS}%")
    log.info(f"  Sinal: EMA(12/26) + 1/3 (RSI<{RSI_BUY}, Vol>{VOL_FACTOR}x, MACD)")
    log.info(f"  Multi-TF: {TIMEFRAME_PRINCIPAL} principal + {TIMEFRAME_CONFIRM} confirmação")
    log.info("="*60)

    if not TELEGRAM_TOKEN:
        log.warning("⚠️  TELEGRAM_TOKEN não definido — notificações desativadas")
    if not FINNHUB_API_KEY:
        log.warning("⚠️  FINNHUB_API_KEY não definida — usando calendário fixo")

    exchange   = conectar()
    carteira   = CarteiraMulti(SALDO_INICIAL)
    gestor     = GestorDiario(SALDO_INICIAL)
    calendario = CalendarioEconomico()
    ciclo      = 0
    precos     = {}

    telegram(
        f"🤖 <b>Crypto Bot v7.1 iniciado!</b>\n"
        f"🏦 Exchange: Binance Futures\n"
        f"📊 {len(SYMBOLS)} pares ativos (vol >{VOLUME_MINIMO_24H/1e6:.0f}M USDT/24h)\n"
        f"💰 Saldo: {SALDO_INICIAL:.2f} USDT\n"
        f"🛑 Stop Loss: -{STOP_LOSS_PCT}% | 🎯 Stop Gain: +{STOP_GAIN_PCT}%\n"
        f"🔄 Trailing: ativa após +{TRAILING_PCT}%\n"
        f"📅 Meta dia: +{DAILY_GAIN}% | Limite: -{DAILY_LOSS}%\n"
        f"⏱️  Timeframe: {TIMEFRAME_PRINCIPAL} + confirmação {TIMEFRAME_CONFIRM}\n"
        f"─────────────────\n"
        f"Bot rodando 24/7 🚀"
    )

    try:
        while True:
            ciclo += 1
            log.info(f"\n─── Ciclo #{ciclo} {'─'*40}")
            try:
                # Atualizar preços
                for sym in SYMBOLS:
                    try:
                        precos[sym] = preco_atual(exchange, sym)
                        time.sleep(0.2)
                    except Exception as e:
                        log.warning(f"Preço {sym}: {e}")

                tot = carteira.total(precos)
                pnl = ((tot - carteira.saldo_ini) / carteira.saldo_ini) * 100
                log.info(f"💰 {tot:.2f} USDT ({pnl:+.2f}%) | Pos:{len(carteira.posicoes)}/{MAX_POSICOES}")

                gestor.verificar_reset(tot)
                calendario.verificar_atualizacao()
                recarregar_symbols_se_necessario(exchange)  # recarga diária

                # Verificar calendário
                em_risco, motivo_risco = calendario.em_zona_de_risco()
                if em_risco:
                    log.warning(f"⚠️  ZONA DE RISCO: {motivo_risco}")
                    if carteira.posicoes:
                        carteira.fechar_todas(precos, "CALENDARIO")
                        telegram(f"⚠️ <b>Zona de risco!</b>\n{motivo_risco}\nPosições fechadas por segurança.")
                    time.sleep(CHECK_EVERY)
                    continue

                # Meta diária
                if gestor.verificar_limites(tot):
                    if carteira.posicoes:
                        carteira.fechar_todas(precos)
                    time.sleep(CHECK_EVERY)
                    continue

                # Verificar stops e trailing
                for sym in list(carteira.posicoes.keys()):
                    if sym in precos:
                        stop = carteira.posicoes[sym].verificar_stops(precos[sym])
                        if stop in ("STOP_LOSS", "STOP_GAIN"):
                            carteira.fechar(sym, precos[sym], stop)

                # Analisar pares
                sinais = []
                for sym in SYMBOLS:
                    try:
                        df1h = calcular_indicadores(
                            obter_candles(exchange, sym, TIMEFRAME_PRINCIPAL, limit=150)
                        )
                        sinal, conf, motivos = avaliar_sinal(df1h)

                        log.info(
                            f"   {sym:10} | EMA:{conf.get('ema','?'):5} | "
                            f"RSI:{conf.get('rsi',0):5.1f} | "
                            f"Vol:{conf.get('vol_ratio',0):.2f}x | "
                            f"MACD:{conf.get('macd_hist',0):+.5f} | "
                            f"L:{conf.get('buy_score',0)}/4 S:{conf.get('sell_score',0)}/4 → {sinal}"
                        )

                        if motivos:
                            log.info(f"            ⏸️  {' | '.join(motivos)}")

                        log_ev({"type":"SCAN","symbol":sym,
                                "price":precos.get(sym,0),"signal":sinal,**conf})

                        if sinal in ("LONG","SHORT") and sym not in carteira.posicoes:
                            # Confirmação no timeframe menor
                            confirma = confirmar_timeframe_menor(exchange, sym)
                            log.info(f"            📊 Confirmação 15m: {confirma}")
                            if confirma == sinal:
                                sinais.append((sym, conf, sinal))
                            else:
                                log.info(f"            ❌ 15m não confirmou ({confirma}) — aguardando")

                        elif sinal == "LONG" and sym in carteira.posicoes and carteira.posicoes[sym].direcao == "SHORT":
                            carteira.fechar(sym, precos[sym], "REVERSAO", conf)
                        elif sinal == "SHORT" and sym in carteira.posicoes and carteira.posicoes[sym].direcao == "LONG":
                            carteira.fechar(sym, precos[sym], "REVERSAO", conf)

                        time.sleep(0.5)
                    except Exception as e:
                        log.warning(f"   Erro {sym}: {e}")

                # Executar melhores sinais
                # Prioriza sinais com EMA cruzando (mais força)
                sinais.sort(key=lambda x: (x[1].get("ema_cruzou", False), x[1].get("buy_score",0) + x[1].get("sell_score",0)), reverse=True)

                for sym, conf, direcao in sinais:
                    if precos.get(sym) and sym not in carteira.posicoes:
                        carteira.abrir(sym, precos[sym], conf, direcao)

                if not sinais:
                    log.info("   ⏸️  Nenhum sinal confirmado nos dois timeframes")

                log.info(f"   📊 {gestor.status(tot)}")
                log.info(f"   🗓️  {calendario.proximo_evento()}")

            except Exception as e:
                log.error(f"Erro no ciclo: {e}", exc_info=True)

            time.sleep(CHECK_EVERY)

    except KeyboardInterrupt:
        log.info("\n⛔ Bot interrompido pelo usuário.")
        carteira.resumo(precos)


if __name__ == "__main__":
    main()
