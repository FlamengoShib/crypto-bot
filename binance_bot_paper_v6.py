"""
╔══════════════════════════════════════════════════════════╗
║      SENAC CRYPTO BOT — PAPER TRADING v6.2               ║
║   Exchange: BYBIT (sem restrição geográfica)             ║
║   EMA + RSI + Volume + MACD — 4/4 obrigatório            ║
║   Multi-Asset 12 pares · 1 posição · 98%                 ║
║   Calendário Econômico + Pausa automática                ║
╚══════════════════════════════════════════════════════════╝
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
#  CONFIGURAÇÕES
# ─────────────────────────────────────────────

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "SUA_CHAVE_FINNHUB_AQUI")

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT",  "SOL/USDT",
    "AVAX/USDT","XRP/USDT", "DOGE/USDT", "SHIB/USDT",
    "ADA/USDT", "TRX/USDT", "LINK/USDT", "LTC/USDT",
]

SALDO_INICIAL  = 100.0
TRADE_PCT      = 0.98
MAX_POSICOES   = 1
STOP_LOSS_PCT  = 1.5
STOP_GAIN_PCT  = 3.0
TIMEFRAME      = '15m'
CHECK_EVERY    = 60

EMA_FAST   = 9
EMA_SLOW   = 21
RSI_PERIOD = 14
RSI_BUY    = 45
RSI_SELL   = 60
VOL_FACTOR = 1.3

DAILY_LOSS  = 5.0
DAILY_GAIN  = 12.0

PAUSA_ANTES  = 30
PAUSA_DEPOIS = 30

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


# ─────────────────────────────────────────────
#  CONEXÃO VIA CCXT (sem restrição geográfica)
# ─────────────────────────────────────────────

def conectar():
    """
    Bybit não tem restrição geográfica — funciona em qualquer servidor.
    Dados públicos de mercado idênticos à Binance.
    """
    exchange = ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
    })
    ticker = exchange.fetch_ticker('BTC/USDT')
    log.info(f"✅ Conectado via Bybit — BTC: {ticker['last']:.2f} USDT")
    return exchange


def obter_candles(exchange, symbol, timeframe='15m', limit=100) -> pd.DataFrame:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['close']  = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    df['high']   = df['high'].astype(float)
    df['low']    = df['low'].astype(float)
    return df


def preco_atual(exchange, symbol) -> float:
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker['last'])


# ─────────────────────────────────────────────
#  INDICADORES
# ─────────────────────────────────────────────

def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    df["ema_fast"]   = ta.trend.EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema_slow"]   = ta.trend.EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi"]        = ta.momentum.RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    df["vol_ma"]     = df["volume"].rolling(20).mean()
    macd             = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
    df["macd_linha"] = macd.macd()
    df["macd_sinal"] = macd.macd_signal()
    df["macd_hist"]  = macd.macd_diff()
    return df


def avaliar_sinal(df: pd.DataFrame) -> tuple[str, dict]:
    last = df.iloc[-1]
    ema_alta   = last["ema_fast"] > last["ema_slow"]
    ema_baixa  = last["ema_fast"] < last["ema_slow"]
    rsi_buy    = last["rsi"] < RSI_BUY
    rsi_sell   = last["rsi"] > RSI_SELL
    vol_ok     = last["volume"] > last["vol_ma"] * VOL_FACTOR
    macd_buy   = last["macd_linha"] > last["macd_sinal"] and last["macd_hist"] > 0
    macd_sell  = last["macd_linha"] < last["macd_sinal"] and last["macd_hist"] < 0

    buy_score  = sum([ema_alta,  rsi_buy,  vol_ok, macd_buy ])
    sell_score = sum([ema_baixa, rsi_sell, vol_ok, macd_sell])

    conf = {
        "ema": "ALTA" if ema_alta else "BAIXA",
        "rsi": round(float(last["rsi"]), 1),
        "vol_ratio": round(float(last["volume"]) / float(last["vol_ma"]), 2) if last["vol_ma"] > 0 else 0,
        "macd_hist": round(float(last["macd_hist"]), 6),
        "buy_score": buy_score, "sell_score": sell_score,
    }
    if buy_score == 4:  return "BUY",  conf
    if sell_score == 4: return "SELL", conf
    return "HOLD", conf


# ─────────────────────────────────────────────
#  CALENDÁRIO ECONÔMICO
# ─────────────────────────────────────────────

class CalendarioEconomico:
    def __init__(self):
        self.eventos_hoje    = []
        self.ultimo_fetch    = None
        self._carregar_eventos()

    def _carregar_eventos(self):
        if FINNHUB_API_KEY == "SUA_CHAVE_FINNHUB_AQUI":
            self._eventos_fixos(); return
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
                     "impact": e.get("impact",""), "pais": e.get("country","US")}
                    for e in eventos
                    if e.get("impact","").lower() in ("high","medium")
                    and any(k in (e.get("event") or "").lower() for k in EVENTOS_CRIPTO)
                ]
                self.ultimo_fetch = time.time()
                log.info(f"📅 {len(self.eventos_hoje)} eventos relevantes hoje")
                for ev in self.eventos_hoje:
                    log.info(f"   ⏰ {ev['hora']} — {ev['nome']}")
            else:
                self._eventos_fixos()
        except Exception as e:
            log.warning(f"⚠️  Calendário: {e}")
            self._eventos_fixos()

    def _eventos_fixos(self):
        dia = datetime.now().weekday()
        self.eventos_hoje = []
        if dia == 4:
            self.eventos_hoje.append({"nome":"Non-Farm Payroll","hora":"09:30","impact":"high","pais":"US"})
        if dia == 2:
            self.eventos_hoje.append({"nome":"FOMC Statement","hora":"15:00","impact":"high","pais":"US"})
            self.eventos_hoje.append({"nome":"CPI Inflation","hora":"09:30","impact":"high","pais":"US"})
        log.info(f"📅 Usando eventos fixos: {len(self.eventos_hoje)}")

    def verificar_atualizacao(self):
        if self.ultimo_fetch and (time.time() - self.ultimo_fetch) > 3600:
            self._carregar_eventos()

    def em_zona_de_risco(self) -> tuple[bool, str]:
        agora = datetime.now()
        for ev in self.eventos_hoje:
            try:
                partes  = ev.get("hora","").split(":")
                hora_ev = agora.replace(hour=int(partes[0]),
                    minute=int(partes[1]) if len(partes)>1 else 0, second=0, microsecond=0)
                inicio  = hora_ev - timedelta(minutes=PAUSA_ANTES)
                fim     = hora_ev + timedelta(minutes=PAUSA_DEPOIS)
                if inicio <= agora <= fim:
                    falta = int((hora_ev - agora).total_seconds() / 60) if agora < hora_ev else 0
                    msg   = f"{ev['nome']} em {falta}min" if falta > 0 else f"Pós-evento {ev['nome']}"
                    return True, msg
            except Exception:
                continue
        return False, ""

    def proximo_evento(self) -> str:
        agora   = datetime.now()
        proximos = []
        for ev in self.eventos_hoje:
            try:
                partes  = ev.get("hora","").split(":")
                hora_ev = agora.replace(hour=int(partes[0]),
                    minute=int(partes[1]) if len(partes)>1 else 0, second=0, microsecond=0)
                if hora_ev > agora:
                    falta = int((hora_ev - agora).total_seconds() / 60)
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

    def verificar_limites(self, saldo) -> bool:
        if self.bloqueado: return True
        pnl = ((saldo - self.saldo_inicio_dia) / self.saldo_inicio_dia) * 100
        if pnl <= -DAILY_LOSS:
            self.bloqueado = True; self.motivo = f"LOSS -{DAILY_LOSS}%"
            log.warning(f"🛑 LIMITE LOSS: {pnl:.2f}%")
            log_ev({"type":"DAILY_BLOCKED","motivo":"LOSS","pnl":round(pnl,2)})
            return True
        if pnl >= DAILY_GAIN:
            self.bloqueado = True; self.motivo = f"GAIN +{DAILY_GAIN}%"
            log.info(f"🎯 META GAIN: +{pnl:.2f}%")
            log_ev({"type":"DAILY_BLOCKED","motivo":"GAIN","pnl":round(pnl,2)})
            return True
        return False

    def status(self, saldo):
        pnl = ((saldo - self.saldo_inicio_dia) / self.saldo_inicio_dia) * 100
        return f"P&L hoje: {pnl:+.2f}% | Meta:+{DAILY_GAIN}% | Limite:-{DAILY_LOSS}%"


# ─────────────────────────────────────────────
#  POSIÇÃO E CARTEIRA
# ─────────────────────────────────────────────

class Posicao:
    def __init__(self, symbol, preco, usdt):
        self.symbol        = symbol
        self.preco_entrada = preco
        self.qty           = usdt / preco
        self.usdt_alocado  = usdt
        self.stop_loss     = preco * (1 - STOP_LOSS_PCT/100)
        self.stop_gain     = preco * (1 + STOP_GAIN_PCT/100)

    def verificar_stops(self, preco):
        if preco <= self.stop_loss: return "STOP_LOSS"
        if preco >= self.stop_gain: return "STOP_GAIN"
        return "OK"

    def pnl_pct(self, preco):
        return ((preco - self.preco_entrada) / self.preco_entrada) * 100


class CarteiraMulti:
    def __init__(self, saldo_ini):
        self.saldo      = saldo_ini
        self.saldo_ini  = saldo_ini
        self.posicoes   = {}
        self.trades     = []
        log.info(f"💼 Carteira: {saldo_ini:.2f} USDT | {len(SYMBOLS)} pares")
        log_ev({"type":"PAPER_START","saldo":saldo_ini,"symbols":SYMBOLS})

    def total(self, precos):
        return self.saldo + sum(
            p.qty * precos.get(s, p.preco_entrada)
            for s, p in self.posicoes.items()
        )

    def abrir(self, symbol, preco, conf):
        if symbol in self.posicoes or len(self.posicoes) >= MAX_POSICOES: return
        usdt = self.saldo_ini * TRADE_PCT
        if self.saldo < usdt:
            log.warning(f"⚠️  Saldo insuficiente: {symbol}"); return
        self.saldo -= usdt
        self.posicoes[symbol] = Posicao(symbol, preco, usdt)
        pos = self.posicoes[symbol]
        log.info(f"🟢 COMPRA {symbol} @ {preco:.4f} | SL:{pos.stop_loss:.4f} | SG:{pos.stop_gain:.4f}")
        log_ev({"type":"BUY","symbol":symbol,"price":preco,"usdt":round(usdt,2),
                "sl":round(pos.stop_loss,4),"sg":round(pos.stop_gain,4)})

    def fechar(self, symbol, preco, motivo, conf={}):
        if symbol not in self.posicoes: return
        pos      = self.posicoes[symbol]
        recebido = pos.qty * preco
        self.saldo += recebido
        pnl      = pos.pnl_pct(preco)
        pnl_usdt = recebido - pos.usdt_alocado
        self.trades.append({"symbol":symbol,"pnl_pct":pnl,"motivo":motivo})
        emoji = "🎯" if motivo=="STOP_GAIN" else "🛑" if motivo=="STOP_LOSS" else "📅" if motivo=="CALENDARIO" else "🔴"
        log.info(f"{emoji} VENDA {symbol} ({motivo}) @ {preco:.4f} | P&L:{pnl:+.2f}% ({pnl_usdt:+.2f} USDT)")
        log_ev({"type":motivo if "STOP" in motivo else "SELL","symbol":symbol,
                "price":preco,"pnl_pct":round(pnl,2),"pnl_usdt":round(pnl_usdt,2)})
        del self.posicoes[symbol]

    def fechar_todas(self, precos, motivo="CALENDARIO"):
        for sym in list(self.posicoes.keys()):
            self.fechar(sym, precos.get(sym, self.posicoes[sym].preco_entrada), motivo)

    def resumo(self, precos):
        tot  = self.total(precos)
        pnl  = ((tot - self.saldo_ini) / self.saldo_ini) * 100
        wins = sum(1 for t in self.trades if t["pnl_pct"] > 0)
        wr   = wins/len(self.trades)*100 if self.trades else 0
        log.info("="*60)
        log.info(f"  Saldo final: {tot:.2f} USDT | P&L: {pnl:+.2f}% | WR: {wr:.0f}%")
        log.info("="*60)
        log_ev({"type":"SUMMARY","saldo_final":round(tot,2),"pnl":round(pnl,2),"wr":round(wr,1)})


# ─────────────────────────────────────────────
#  LOOP PRINCIPAL
# ─────────────────────────────────────────────

def main():
    log.info("="*60)
    log.info("  🤖 CRYPTO BOT MULTI-ASSET v6.1 — CCXT + PAPER TRADING")
    log.info(f"  {len(SYMBOLS)} pares | SL:-{STOP_LOSS_PCT}% | SG:+{STOP_GAIN_PCT}%")
    log.info(f"  Meta:+{DAILY_GAIN}% | Limite:-{DAILY_LOSS}%")
    log.info(f"  Calendário: ±{PAUSA_ANTES}min em eventos de risco")
    log.info("="*60)

    exchange   = conectar()
    carteira   = CarteiraMulti(SALDO_INICIAL)
    gestor     = GestorDiario(SALDO_INICIAL)
    calendario = CalendarioEconomico()
    ciclo      = 0
    precos     = {}

    try:
        while True:
            ciclo += 1
            log.info(f"\n─── Ciclo #{ciclo} {'─'*40}")
            try:
                # Buscar preços
                for sym in SYMBOLS:
                    try:
                        precos[sym] = preco_atual(exchange, sym)
                        time.sleep(0.2)
                    except Exception as e:
                        log.warning(f"Preço {sym}: {e}")

                tot = carteira.total(precos)
                pnl = ((tot - carteira.saldo_ini) / carteira.saldo_ini) * 100
                log.info(f"💰 {tot:.2f} USDT ({pnl:+.2f}%) | Pos:{len(carteira.posicoes)}")

                gestor.verificar_reset(tot)
                calendario.verificar_atualizacao()

                # Calendário
                em_risco, motivo_risco = calendario.em_zona_de_risco()
                if em_risco:
                    log.warning(f"⚠️  ZONA DE RISCO: {motivo_risco}")
                    if carteira.posicoes:
                        carteira.fechar_todas(precos, "CALENDARIO")
                        log_ev({"type":"CALENDAR_PAUSE","motivo":motivo_risco})
                    log.info(f"   ⏸️  {calendario.proximo_evento()}")
                    time.sleep(CHECK_EVERY)
                    continue

                # Meta diária
                if gestor.verificar_limites(tot):
                    log.info(f"⏸️  [{gestor.motivo}]")
                    time.sleep(CHECK_EVERY)
                    continue

                # Stops
                for sym in list(carteira.posicoes.keys()):
                    if sym in precos:
                        stop = carteira.posicoes[sym].verificar_stops(precos[sym])
                        if stop in ("STOP_LOSS","STOP_GAIN"):
                            carteira.fechar(sym, precos[sym], stop)

                # Analisar pares
                sinais_buy, sinais_sell = [], []
                for sym in SYMBOLS:
                    try:
                        df = calcular_indicadores(obter_candles(exchange, sym, TIMEFRAME))
                        sinal, conf = avaliar_sinal(df)
                        log.info(f"   {sym:10} | {conf['ema']:5} | RSI:{conf['rsi']:5.1f} | "
                                 f"Vol:{conf['vol_ratio']:.2f}x | "
                                 f"{conf['buy_score']}▲{conf['sell_score']}▼ → {sinal}")
                        log_ev({"type":"SCAN","symbol":sym,"price":precos.get(sym,0),
                                "signal":sinal,**conf})
                        if sinal=="BUY"  and sym not in carteira.posicoes: sinais_buy.append((sym,conf))
                        if sinal=="SELL" and sym in carteira.posicoes:     sinais_sell.append((sym,conf))
                        time.sleep(0.5)
                    except Exception as e:
                        log.warning(f"   Erro {sym}: {e}")

                for sym, conf in sinais_sell: carteira.fechar(sym, precos[sym], "SELL", conf)
                for sym, conf in sinais_buy:
                    if precos.get(sym): carteira.abrir(sym, precos[sym], conf)

                if not sinais_buy and not sinais_sell:
                    log.info("   ⏸️  Nenhum sinal 4/4")

                log.info(f"   📊 {gestor.status(tot)}")
                log.info(f"   🗓️  {calendario.proximo_evento()}")

            except Exception as e:
                log.error(f"Erro: {e}", exc_info=True)

            time.sleep(CHECK_EVERY)

    except KeyboardInterrupt:
        log.info("\n⛔ Bot interrompido.")
        carteira.resumo(precos)


if __name__ == "__main__":
    main()
