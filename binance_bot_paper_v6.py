"""
╔══════════════════════════════════════════════════════════╗
║      SENAC CRYPTO BOT — PAPER TRADING v6.0               ║
║   EMA + RSI + Volume + MACD — 4/4 obrigatório            ║
║   Multi-Asset 12 pares · 1 posição · 98%                 ║
║   Calendário Econômico + Pausa automática                ║
╚══════════════════════════════════════════════════════════╝

CALENDÁRIO ECONÔMICO:
  - Consulta Finnhub API (gratuita) para eventos do dia
  - Pausa 30min ANTES e 30min APÓS eventos de alto impacto
  - Fecha posição aberta ao entrar na janela de risco
  - Retoma automaticamente após a janela passar
  - Fallback com horários fixos se API não configurada

COMO OBTER A CHAVE FINNHUB (GRATUITO):
  1. Acesse https://finnhub.io
  2. Clique em "Get free API key"
  3. Cadastre com e-mail
  4. Cole a chave em FINNHUB_API_KEY abaixo

REQUISITOS:
    pip install python-binance pandas ta requests
"""

import os
import time
import logging
import json
import requests
from datetime import datetime, timedelta
from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import ta

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES
#  No Railway: Settings → Variables → adicione as chaves
#  Localmente: preencha direto aqui
# ─────────────────────────────────────────────

# Lê das variáveis de ambiente do Railway — ou usa valor direto
API_KEY         = os.getenv("BINANCE_API_KEY",    "")
API_SECRET      = os.getenv("BINANCE_API_SECRET", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY",    "SUA_CHAVE_FINNHUB_AQUI")

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT",  "SOLUSDT",
    "AVAXUSDT","XRPUSDT", "DOGEUSDT", "SHIBUSDT",
    "ADAUSDT", "TRXUSDT", "LINKUSDT", "LTCUSDT",
]

SALDO_INICIAL_USDT = 100.0
TRADE_PCT          = 0.98
MAX_POSICOES       = 1
STOP_LOSS_PCT      = 1.5
STOP_GAIN_PCT      = 3.0
INTERVAL           = Client.KLINE_INTERVAL_15MINUTE
CHECK_EVERY_SEC    = 60

EMA_FAST   = 9
EMA_SLOW   = 21
RSI_PERIOD = 14
RSI_BUY    = 45
RSI_SELL   = 60
VOL_FACTOR = 1.3

DAILY_LOSS_LIMIT_PCT  = 5.0
DAILY_GAIN_TARGET_PCT = 12.0

PAUSA_ANTES_MIN  = 30
PAUSA_DEPOIS_MIN = 30

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
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot_console.log")
    ]
)
log = logging.getLogger("CryptoBot")


def log_evento(event: dict):
    event["timestamp"] = datetime.utcnow().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


# ─────────────────────────────────────────────
#  CALENDÁRIO ECONÔMICO
# ─────────────────────────────────────────────

class CalendarioEconomico:
    def __init__(self):
        self.eventos_hoje    = []
        self.ultimo_fetch    = None
        self.fetch_intervalo = 3600
        log.info(f"📅 Calendário econômico ativado")
        log.info(f"   Janela de proteção: {PAUSA_ANTES_MIN}min antes / {PAUSA_DEPOIS_MIN}min depois")
        self._carregar_eventos()

    def _carregar_eventos(self):
        if FINNHUB_API_KEY == "SUA_CHAVE_FINNHUB_AQUI":
            log.warning("⚠️  Finnhub não configurada — usando eventos fixos como fallback")
            self._eventos_fixos()
            return
        try:
            hoje   = datetime.now().strftime("%Y-%m-%d")
            resp   = requests.get(
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
                    log.info(f"   ⏰ {ev['hora']} — {ev['nome']} [{ev['impact'].upper()}]")
                    log_evento({"type": "CALENDAR_EVENT", **ev})
            else:
                log.warning(f"⚠️  Finnhub erro {resp.status_code} — usando eventos fixos")
                self._eventos_fixos()
        except Exception as e:
            log.warning(f"⚠️  Erro calendário: {e} — usando eventos fixos")
            self._eventos_fixos()

    def _eventos_fixos(self):
        """Horários recorrentes de alto impacto (horário de Brasília)."""
        dia = datetime.now().weekday()
        self.eventos_hoje = []
        # NFP — primeira sexta do mês, 09h30 Brasília
        if dia == 4:
            self.eventos_hoje.append({"nome":"Non-Farm Payroll (possível)",
                "hora":"09:30","impact":"high","pais":"US"})
        # FOMC — quarta, 15h Brasília
        if dia == 2:
            self.eventos_hoje.append({"nome":"FOMC Statement (possível)",
                "hora":"15:00","impact":"high","pais":"US"})
        # CPI — quarta, 09h30 Brasília
        if dia == 2:
            self.eventos_hoje.append({"nome":"CPI Inflation (possível)",
                "hora":"09:30","impact":"high","pais":"US"})
        log.info(f"📅 Eventos fixos: {len(self.eventos_hoje)}")

    def verificar_atualizacao(self):
        if self.ultimo_fetch and (time.time() - self.ultimo_fetch) > self.fetch_intervalo:
            log.info("📅 Atualizando calendário...")
            self._carregar_eventos()

    def em_zona_de_risco(self) -> tuple[bool, str]:
        agora = datetime.now()
        for ev in self.eventos_hoje:
            try:
                hora_str = ev.get("hora","")
                if not hora_str or hora_str == "Tentative":
                    continue
                partes  = hora_str.split(":")
                hora_ev = agora.replace(
                    hour=int(partes[0]), minute=int(partes[1]) if len(partes)>1 else 0,
                    second=0, microsecond=0
                )
                inicio = hora_ev - timedelta(minutes=PAUSA_ANTES_MIN)
                fim    = hora_ev + timedelta(minutes=PAUSA_DEPOIS_MIN)
                if inicio <= agora <= fim:
                    if agora < hora_ev:
                        falta  = int((hora_ev - agora).total_seconds() / 60)
                        return True, f"{ev['nome']} em {falta}min ({ev['hora']})"
                    else:
                        passou = int((agora - hora_ev).total_seconds() / 60)
                        return True, f"Pós-evento {ev['nome']} ({passou}min atrás)"
            except Exception:
                continue
        return False, ""

    def proximo_evento(self) -> str:
        agora = datetime.now()
        proximos = []
        for ev in self.eventos_hoje:
            try:
                partes  = ev.get("hora","").split(":")
                hora_ev = agora.replace(
                    hour=int(partes[0]), minute=int(partes[1]) if len(partes)>1 else 0,
                    second=0, microsecond=0
                )
                if hora_ev > agora:
                    falta = int((hora_ev - agora).total_seconds() / 60)
                    proximos.append((falta, f"{ev['nome']} às {ev['hora']} (em {falta}min)"))
            except Exception:
                continue
        return proximos[0][1] if proximos else "Nenhum evento relevante hoje"


# ─────────────────────────────────────────────
#  GESTOR DIÁRIO
# ─────────────────────────────────────────────

class GestorDiario:
    def __init__(self, saldo_inicial):
        self.saldo_inicio_dia = saldo_inicial
        self.data_atual       = datetime.now().date()
        self.bloqueado        = False
        self.motivo_bloqueio  = ""

    def verificar_reset(self, saldo_atual):
        hoje = datetime.now().date()
        if hoje > self.data_atual:
            log.info(f"🌅 Novo dia — meta resetada! Ref: {saldo_atual:.2f}")
            self.saldo_inicio_dia = saldo_atual
            self.data_atual       = hoje
            self.bloqueado        = False
            self.motivo_bloqueio  = ""
            log_evento({"type":"DAILY_RESET","saldo":round(saldo_atual,2)})

    def verificar_limites(self, saldo_atual) -> bool:
        if self.bloqueado: return True
        pnl = ((saldo_atual - self.saldo_inicio_dia) / self.saldo_inicio_dia) * 100
        if pnl <= -DAILY_LOSS_LIMIT_PCT:
            self.bloqueado = True; self.motivo_bloqueio = f"LOSS -{DAILY_LOSS_LIMIT_PCT}%"
            log.warning(f"🛑 LIMITE LOSS: {pnl:.2f}%")
            log_evento({"type":"DAILY_BLOCKED","motivo":"LOSS","pnl":round(pnl,2)})
            return True
        if pnl >= DAILY_GAIN_TARGET_PCT:
            self.bloqueado = True; self.motivo_bloqueio = f"GAIN +{DAILY_GAIN_TARGET_PCT}%"
            log.info(f"🎯 META GAIN: +{pnl:.2f}%")
            log_evento({"type":"DAILY_BLOCKED","motivo":"GAIN","pnl":round(pnl,2)})
            return True
        return False

    def status(self, saldo_atual):
        pnl = ((saldo_atual - self.saldo_inicio_dia) / self.saldo_inicio_dia) * 100
        return f"P&L hoje: {pnl:+.2f}% | Meta: +{DAILY_GAIN_TARGET_PCT}% | Limite: -{DAILY_LOSS_LIMIT_PCT}%"


# ─────────────────────────────────────────────
#  CONEXÃO E INDICADORES
# ─────────────────────────────────────────────

def conectar():
    client = Client(API_KEY, API_SECRET)
    client.ping()
    log.info("✅ Conectado à Binance.")
    return client


def obter_candles(client, symbol, interval, limit=100):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ["close","volume","high","low","open"]:
        df[col] = df[col].astype(float)
    return df


def calcular_indicadores(df):
    df["ema_fast"]   = ta.trend.EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema_slow"]   = ta.trend.EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi"]        = ta.momentum.RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    df["vol_ma"]     = df["volume"].rolling(20).mean()
    macd             = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
    df["macd_linha"] = macd.macd()
    df["macd_sinal"] = macd.macd_signal()
    df["macd_hist"]  = macd.macd_diff()
    return df


def avaliar_sinal(df):
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
        "rsi": round(last["rsi"],1),
        "vol_ratio": round(last["volume"]/last["vol_ma"],2) if last["vol_ma"]>0 else 0,
        "macd_hist": round(last["macd_hist"],4),
        "buy_score": buy_score, "sell_score": sell_score,
    }
    if buy_score == 4:  return "BUY",  conf
    if sell_score == 4: return "SELL", conf
    return "HOLD", conf


def preco_atual(client, symbol):
    return float(client.get_symbol_ticker(symbol=symbol)["price"])


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
    def __init__(self, saldo_inicial):
        self.saldo_usdt    = saldo_inicial
        self.saldo_inicial = saldo_inicial
        self.posicoes      = {}
        self.trades        = []
        log.info(f"💼 Carteira: {saldo_inicial:.2f} USDT | {len(SYMBOLS)} pares monitorados")
        log_evento({"type":"PAPER_START","saldo_inicial":saldo_inicial,"symbols":SYMBOLS})

    def saldo_total(self, precos):
        return self.saldo_usdt + sum(
            p.qty * precos[s] for s, p in self.posicoes.items() if s in precos
        )

    def abrir(self, symbol, preco, conf):
        if symbol in self.posicoes or len(self.posicoes) >= MAX_POSICOES: return
        usdt = self.saldo_inicial * TRADE_PCT
        if self.saldo_usdt < usdt:
            log.warning(f"⚠️  Saldo insuficiente para {symbol}"); return
        self.saldo_usdt -= usdt
        pos = Posicao(symbol, preco, usdt)
        self.posicoes[symbol] = pos
        log.info(f"🟢 COMPRA {symbol} @ {preco:.4f} | SL:{pos.stop_loss:.4f} | SG:{pos.stop_gain:.4f}")
        log_evento({"type":"BUY","symbol":symbol,"price":preco,"usdt":round(usdt,2),
                    "stop_loss":round(pos.stop_loss,4),"stop_gain":round(pos.stop_gain,4),"conf":conf})

    def fechar(self, symbol, preco, motivo, conf={}):
        if symbol not in self.posicoes: return
        pos      = self.posicoes[symbol]
        recebido = pos.qty * preco
        self.saldo_usdt += recebido
        pnl_pct  = pos.pnl_pct(preco)
        pnl_usdt = recebido - pos.usdt_alocado
        self.trades.append({"symbol":symbol,"pnl_pct":pnl_pct,"motivo":motivo})
        emoji = "🎯" if motivo=="STOP_GAIN" else "🛑" if motivo=="STOP_LOSS" else "📅" if motivo=="CALENDARIO" else "🔴"
        log.info(f"{emoji} VENDA {symbol} ({motivo}) @ {preco:.4f} | P&L:{pnl_pct:+.2f}% ({pnl_usdt:+.2f} USDT)")
        log_evento({"type":motivo if "STOP" in motivo else "SELL","symbol":symbol,
                    "price":preco,"pnl_pct":round(pnl_pct,2),"pnl_usdt":round(pnl_usdt,2)})
        del self.posicoes[symbol]

    def fechar_todas(self, precos, motivo="CALENDARIO"):
        for sym in list(self.posicoes.keys()):
            if sym in precos:
                self.fechar(sym, precos[sym], motivo)

    def resumo(self, precos):
        total   = self.saldo_total(precos)
        pnl_pct = ((total - self.saldo_inicial) / self.saldo_inicial) * 100
        wins    = sum(1 for t in self.trades if t["pnl_pct"] > 0)
        wr      = wins/len(self.trades)*100 if self.trades else 0
        log.info("="*60)
        log.info(f"  Saldo final: {total:.2f} USDT | P&L: {pnl_pct:+.2f}%")
        log.info(f"  Trades: {len(self.trades)} | Win rate: {wr:.0f}%")
        log.info("="*60)
        log_evento({"type":"SUMMARY","saldo_final":round(total,2),
                    "pnl":round(pnl_pct,2),"trades":len(self.trades),"win_rate":round(wr,1)})


# ─────────────────────────────────────────────
#  LOOP PRINCIPAL
# ─────────────────────────────────────────────

def main():
    log.info("="*60)
    log.info("  🤖 CRYPTO BOT MULTI-ASSET v6.0 — PAPER TRADING")
    log.info(f"  {len(SYMBOLS)} pares | SL:-{STOP_LOSS_PCT}% | SG:+{STOP_GAIN_PCT}%")
    log.info(f"  Meta:+{DAILY_GAIN_TARGET_PCT}% | Limite:-{DAILY_LOSS_LIMIT_PCT}%")
    log.info(f"  Calendário: pausa {PAUSA_ANTES_MIN}min antes / {PAUSA_DEPOIS_MIN}min depois")
    log.info("="*60)

    client     = conectar()
    carteira   = CarteiraMulti(SALDO_INICIAL_USDT)
    gestor     = GestorDiario(SALDO_INICIAL_USDT)
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
                    try: precos[sym] = preco_atual(client, sym)
                    except Exception as e: log.warning(f"Preço {sym}: {e}")

                total = carteira.saldo_total(precos)
                pnl   = ((total - carteira.saldo_inicial) / carteira.saldo_inicial) * 100
                log.info(f"💰 {total:.2f} USDT ({pnl:+.2f}%) | Pos: {len(carteira.posicoes)}")

                gestor.verificar_reset(total)
                calendario.verificar_atualizacao()

                # ── CALENDÁRIO ECONÔMICO ──────────────────────────
                em_risco, motivo_risco = calendario.em_zona_de_risco()
                if em_risco:
                    log.warning(f"⚠️  ZONA DE RISCO: {motivo_risco}")
                    if carteira.posicoes:
                        log.warning("   Fechando posições por segurança...")
                        carteira.fechar_todas(precos, "CALENDARIO")
                        log_evento({"type":"CALENDAR_PAUSE","motivo":motivo_risco})
                    log.info(f"   ⏸️  Próximo: {calendario.proximo_evento()}")
                    time.sleep(CHECK_EVERY_SEC)
                    continue

                # ── META DIÁRIA ───────────────────────────────────
                if gestor.verificar_limites(total):
                    log.info(f"⏸️  [{gestor.motivo_bloqueio}]")
                    time.sleep(CHECK_EVERY_SEC)
                    continue

                # ── STOPS ─────────────────────────────────────────
                for sym in list(carteira.posicoes.keys()):
                    if sym in precos:
                        stop = carteira.posicoes[sym].verificar_stops(precos[sym])
                        if stop in ("STOP_LOSS","STOP_GAIN"):
                            carteira.fechar(sym, precos[sym], stop)

                # ── ANALISAR PARES ────────────────────────────────
                sinais_buy, sinais_sell = [], []
                for sym in SYMBOLS:
                    try:
                        df = calcular_indicadores(obter_candles(client, sym, INTERVAL))
                        sinal, conf = avaliar_sinal(df)
                        log.info(f"   {sym:12} | {conf['ema']:5} | RSI:{conf['rsi']:5.1f} | "
                                 f"Vol:{conf['vol_ratio']:.2f}x | MACD:{'✅' if conf['buy_score']==4 or conf['sell_score']==4 else '─'} | "
                                 f"{conf['buy_score']}▲{conf['sell_score']}▼ → {sinal}")
                        log_evento({"type":"SCAN","symbol":sym,"price":precos.get(sym,0),
                                    "signal":sinal,**conf})
                        if sinal=="BUY"  and sym not in carteira.posicoes: sinais_buy.append((sym,conf))
                        if sinal=="SELL" and sym in carteira.posicoes:     sinais_sell.append((sym,conf))
                        time.sleep(0.3)
                    except Exception as e:
                        log.warning(f"   Erro {sym}: {e}")

                for sym, conf in sinais_sell: carteira.fechar(sym, precos[sym], "SELL", conf)
                for sym, conf in sinais_buy:
                    if precos.get(sym): carteira.abrir(sym, precos[sym], conf)

                if not sinais_buy and not sinais_sell:
                    log.info("   ⏸️  Nenhum sinal 4/4")

                log.info(f"   📊 {gestor.status(total)}")
                log.info(f"   🗓️  {calendario.proximo_evento()}")

            except BinanceAPIException as e:
                log.error(f"API Binance: {e}")
            except Exception as e:
                log.error(f"Erro: {e}", exc_info=True)

            time.sleep(CHECK_EVERY_SEC)

    except KeyboardInterrupt:
        log.info("\n⛔ Bot interrompido.")
        carteira.resumo(precos)


if __name__ == "__main__":
    main()
