#!/usr/bin/env python3
"""
Orquestrator ISA — ChatCommerce Bot v2.1 (CORREGIDO)
FastAPI + WhatsApp Business API + Supabase
7 idiomas: Darija, Arabe, Frances, Espanol, Ingles, Aleman, Turco
Deploy: Render.com | Tetouan, Marruecos
FIX: Campos Pydantic alineados con API en español
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from supabase import create_client, Client
import httpx

# ── Logging ────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("isa-bot")

# ── Variables de entorno ───────────────────
WHATSAPP_TOKEN   = os.getenv("WHATSAPP_TOKEN", "")
VERIFY_TOKEN     = os.getenv("VERIFY_TOKEN", "isa_verify_2026")
PHONE_NUMBER_ID  = os.getenv("PHONE_NUMBER_ID", "")
SUPABASE_URL     = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY     = os.getenv("SUPABASE_KEY", "")
WEBHOOK_PREFIX   = os.getenv("WEBHOOK_PREFIX", "/api/whatsapp/webhook")
META_API_VERSION = os.getenv("META_API_VERSION", "v18.0")

logger.info(f"[INIT] WHATSAPP_TOKEN  : {bool(WHATSAPP_TOKEN)}")
logger.info(f"[INIT] VERIFY_TOKEN    : {VERIFY_TOKEN}")
logger.info(f"[INIT] PHONE_NUMBER_ID : {PHONE_NUMBER_ID}")
logger.info(f"[INIT] SUPABASE_URL    : {bool(SUPABASE_URL)}")
logger.info(f"[INIT] WEBHOOK_PREFIX  : {WEBHOOK_PREFIX}")

# ── Supabase singleton ─────────────────────
_supabase: Optional[Client] = None
def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no configurados")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[SUPABASE] Cliente inicializado")
    return _supabase

# ── Modelos CORREGIDOS: Campos en español con aliases ────────────────────────────────
class ClientCreate(BaseModel):
    nombre: str = Field(..., alias="nombre")
    telefono: str = Field(..., alias="telefono")
    idioma: str = Field(default="darija", alias="idioma")
    tipo_negocio: str = Field(default="restaurant", alias="tipo_negocio")
    zona: str = Field(default="centro", alias="zona")
    plan: str = Field(default="basic", alias="plan")
    
    class Config:
        populate_by_name = True

class MenuItemCreate(BaseModel):
    menu_id: str = Field(..., alias="menu_id")
    nombre: str = Field(..., alias="nombre")
    descripcion: str = Field(default="", alias="descripcion")
    precio: int = Field(..., alias="precio")
    categoria: str = Field(default="", alias="categoria")
    disponible: bool = Field(default=True, alias="disponible")
    
    class Config:
        populate_by_name = True

# ══════════════════════════════════════════════════════════
# NLP — 7 idiomas (SIN CAMBIOS - YA FUNCIONA)
# ══════════════════════════════════════════════════════════
class DarijaNLP:
    INTENTS = {
        "greeting": [
            "salam", "ahlan", "marhba", "marhaba", "labas", "bikhir",
            "sbah lkhir", "msa lkhir", "slamo", "kifach", "kifach lwale",
            "kho", "khti", "salam alaykum",
            "marhaba bik",
            "\u0645\u0631\u062d\u0628\u0627", "\u0633\u0644\u0627\u0645",
            "\u0623\u0647\u0644\u0627", "\u0635\u0628\u0627\u062d \u0627\u0644\u062e\u064a\u0631",
            "\u0645\u0633\u0627\u0621 \u0627\u0644\u062e\u064a\u0631",
            "\u0627\u0644\u0633\u0644\u0627\u0645 \u0639\u0644\u064a\u0643\u0645",
            "bonjour", "salut", "bonsoir", "coucou", "bon matin", "bonne journee",
            "hola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
            "hello", "hi", "hey", "good morning", "good evening", "good afternoon",
            "hallo", "guten tag", "guten abend", "guten morgen", "gruss dich", "servus",
            "merhaba", "selam", "gunaydin", "iyi aksamlar", "nasilsin", "hos geldin",
        ],
        "menu": [
            "menu", "lmenu", "lmaakoul", "chno kayn", "chno 3ndek",
            "chhal dial", "chno 3andkom", "wesh kayn", "chno fih",
            "\u0642\u0627\u0626\u0645\u0629", "\u0645\u0646\u064a\u0648",
            "\u0623\u0637\u0628\u0627\u0642", "\u0645\u0627 \u0639\u0646\u062f\u0643\u0645",
            "carte", "plats", "nourriture", "la carte",
            "carta", "platos", "comida", "que hay", "que tienen",
            "food", "dishes", "what do you have", "options", "catalog",
            "speisekarte", "essen", "gerichte", "was gibt es", "speisen",
            "yemek", "yemekler", "neler var", "secenekler",
        ],
        "order": [
            "bghit", "bghiti", "3tini", "dir liya", "jeb liya",
            "commande", "sed9", "nbghi", "nakhod", "wakha dir",
            "\u0623\u0631\u064a\u062f", "\u0623\u062d\u0628",
            "\u0623\u0639\u0637\u0646\u064a", "\u0627\u0637\u0644\u0628",
            "commander", "je veux", "je voudrais", "donne-moi", "passer commande",
            "pedir", "quiero", "dame", "me pones", "hazme", "ordenar",
            "order", "i want", "i'd like", "give me", "can i get", "i'll have",
            "bestellen", "ich mochte", "geben sie mir", "ich will",
            "siparis", "istiyorum", "almak istiyorum",
        ],
        "help": [
            "3awni", "kifach ndir", "ma fhamteksh", "chno ndir", "wsh hada",
            "ma fahmt", "ayuda",
            "\u0645\u0633\u0627\u0639\u062f\u0629", "\u0643\u064a\u0641",
            "\u0644\u0627 \u0623\u0641\u0647\u0645", "\u0645\u0627\u0630\u0627 \u0623\u0641\u0639\u0644",
            "aide", "comment", "je ne comprends pas", "je ne sais pas",
            "como", "no entiendo", "no se", "que hago",
            "help", "how", "i don't know", "i don't understand", "support",
            "hilfe", "wie", "ich verstehe nicht", "ich weiss nicht",
            "yardim", "nasil", "anlamadim", "bilmiyorum", "destek",
        ],
        "cancel": [
            "lghi", "batal", "safi", "ma bghitsh", "cancel", "wakha ma3andish",
            "\u0625\u0644\u063a\u0627\u0621", "\u0644\u0627 \u0623\u0631\u064a\u062f",
            "annuler", "je ne veux plus", "arrete", "non merci",
            "cancelar", "no quiero", "olvidalo", "no gracias",
            "never mind", "stop", "no thanks", "abort",
            "abbrechen", "stornieren", "ich will nicht", "nein danke",
            "iptal", "istemiyorum", "hayir", "dur", "vazgec",
        ],
        "confirm": [
            "wakha", "ayeh", "mzyan", "mezyan", "safi", "sed9 liya", "iyeh", "aywa",
            "\u0646\u0639\u0645", "\u062a\u0645\u0627\u0645", "\u0645\u0648\u0627\u0641\u0642",
            "oui", "d'accord", "c'est bon", "parfait",
            "vale", "bueno", "confirmo", "perfecto",
            "yes", "yeah", "ok", "sure", "confirm", "agreed",
            "ja", "einverstanden", "perfekt", "bestätigt", "gut",
            "evet", "tamam", "onayliyorum", "peki", "olur",
        ],
        "lista": [
            "lista", "list", "restaurantes",
            "\u0627\u0644\u0645\u0637\u0627\u0639\u0645",
            "restaurants", "ver restaurantes",
            "restoranlar", "restaurants disponibles",
        ],
        "pedido": [
            "pedido", "\u0637\u0644\u0628\u064a", "mon commande", "lcommande",
            "my order", "ver pedido", "mi orden", "siparisim",
        ],
    }
    
    LANG_KEYWORDS = {
        "german":  ["hallo", "guten", "gruss", "servus", "bitte", "danke",
                    "bestellen", "ich", "nein", "hilfe", "danke schon"],
        "turkish": ["merhaba", "selam", "gunaydin", "nasilsin", "tesekkur",
                    "lutfen", "siparis", "evet", "hayir", "istiyorum", "yardim"],
        "english": ["hello", "hi", "hey", "good morning", "thanks", "please",
                    "yes", "no", "order", "help", "food", "i want", "i'd like"],
        "french":  ["bonjour", "salut", "merci", "oui", "non", "je", "tu",
                    "vous", "commander", "aide", "bonsoir", "carte"],
        "spanish": ["hola", "quiero", "buenas", "gracias", "como",
                    "pedir", "cancelar", "ayuda", "menu", "buenos dias"],
    }
    
    @classmethod
    def detect_language(cls, text: str) -> str:
        text_lower = text.lower().strip()
        
        # === DARIJA (romanizado) - PRIORIDAD 1 para Tetouan ===
        darija_keywords = [
            "salam", "salam kho", "salam khti", "bghit", "bghina", "chno", 
            "kifach", "wakha", "ayeh", "la", "bzaf", "chwiya", "f", "w", "wlla",
            "3lik", "3ndi", "7ta", "9albi", "merci bzzaf", "vale safi"
        ]
        if any(kw in text_lower for kw in darija_keywords):
            return "darija"
        
        # === ÁRABE (script árabe) ===
        if any(char in text for char in ["\u0600", "\u0601", "\u0602", "\u0603"]):
            return "arabic"
        arabic_keywords = ["مرحبا", "طلب", "تاكوز", "ساندويتش", "شكرا", "كم", "ثمن"]
        if any(kw in text for kw in arabic_keywords):
            return "arabic"
        
        # === FRANCÉS ===
        fr_keywords = ["bonjour", "salut", "merci", "oui", "non", "je", "vous", "commande", "menu"]
        if any(kw in text_lower for kw in fr_keywords):
            return "french"
        
        # === ESPAÑOL ===
        es_keywords = ["hola", "buenas", "gracias", "sí", "no", "quiero", "pedido", "menú", "taco"]
        if any(kw in text_lower for kw in es_keywords):
            return "spanish"
        
        # === INGLÉS ===
        en_keywords = ["hello", "hi", "thanks", "yes", "no", "want", "order", "menu"]
        if any(kw in text_lower for kw in en_keywords):
            return "english"
        
        # === FALLBACK: Darija (asumimos Tetouan por defecto) ===
        return "darija"
    
    @classmethod
    def detect_intent(cls, text: str) -> str:
        text_lower = text.strip().lower()
        if text_lower.isdigit():
            return "add_item"
        for intent, keywords in cls.INTENTS.items():
            for kw in keywords:
                if kw in text_lower:
                    logger.info(f"[NLP] intent='{intent}' keyword='{kw}'")
                    return intent
        return "unknown"

# ══════════════════════════════════════════════════════════
# BOT LOGIC — Respuestas en 7 idiomas (SIN CAMBIOS)
# ══════════════════════════════════════════════════════════
class BotLogic:
    MSG = {
        "greeting": {
            "arabic":  "\U0001f44b *\u0633\u0644\u0627\u0645! \u0645\u0631\u062d\u0628\u0627 \u0628\u064a\u0643* \U0001f60a\n\u0623\u0646\u0627 \u0645\u0633\u0627\u0639\u062f \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u062f\u064a\u0627\u0644\u0643 \u0641\u0640 WhatsApp.\n\U0001f374 *\u0634\u0648\u0641 \u0627\u0644\u0645\u0646\u064a\u0648* \u2014 \u0643\u062a\u0628 \"menu\"\n\U0001f4cd *\u0627\u0644\u0645\u0637\u0627\u0639\u0645* \u2014 \u0643\u062a\u0628 \"lista\"\n\u2753 *\u0645\u0633\u0627\u0639\u062f\u0629* \u2014 \u0643\u062a\u0628 \"ayuda\"",
            "darija":  "\U0001f44b *Salam! Marhba bik f Orquestrator ISA* \U0001f60a\nAna l'assistant dyalek dyal l-commandes 3la WhatsApp.\n\U0001f374 *Shuf lmenu* \u2014 kteb \"menu\"\n\U0001f4cd *Restaurants* \u2014 kteb \"lista\"\n\u2753 *M3awda* \u2014 kteb \"ayuda\"",
            "french":  "\U0001f44b *Bonjour! Bienvenue chez Orquestrator ISA* \U0001f60a\nJe suis votre assistant de commandes WhatsApp.\n\U0001f374 *Voir le menu* \u2014 tapez \"menu\"\n\U0001f4cd *Restaurants* \u2014 tapez \"lista\"\n\u2753 *Aide* \u2014 tapez \"ayuda\"",
            "spanish": "\U0001f44b *\u00a1Hola! Bienvenido a Orquestrator ISA* \U0001f60a\nSoy tu asistente de pedidos por WhatsApp.\n\U0001f374 *Ver men\u00fa* \u2014 escribe \"menu\"\n\U0001f4cd *Restaurantes* \u2014 escribe \"lista\"\n\u2753 *Ayuda* \u2014 escribe \"ayuda\"",
            "english": "\U0001f44b *Hello! Welcome to Orquestrator ISA* \U0001f60a\nI'm your WhatsApp ordering assistant.\n\U0001f374 *See menu* \u2014 type \"menu\"\n\U0001f4cd *Restaurants* \u2014 type \"lista\"\n\u2753 *Help* \u2014 type \"ayuda\"",
            "german":  "\U0001f44b *Hallo! Willkommen bei Orquestrator ISA* \U0001f60a\nIch bin Ihr WhatsApp-Bestellassistent.\n\U0001f374 *Men\u00fc ansehen* \u2014 tippen Sie \"menu\"\n\U0001f4cd *Restaurants* \u2014 tippen Sie \"lista\"\n\u2753 *Hilfe* \u2014 tippen Sie \"ayuda\"",
            "turkish": "\U0001f44b *Merhaba! Orquestrator ISA'ya ho\u015f geldiniz* \U0001f60a\nBen WhatsApp sipari\u015f asistan\u0131n\u0131z\u0131m.\n\U0001f374 *Men\u00fcy\u00fc g\u00f6r* \u2014 \"menu\" yaz\u0131n\n\U0001f4cd *Restoranlar* \u2014 \"lista\" yaz\u0131n\n\u2753 *Yard\u0131m* \u2014 \"ayuda\" yaz\u0131n",
        },
        "help": {
            "arabic":  "\U0001f4cb *\u0634\u0646\u0648 \u062a\u0642\u062f\u0631 \u062f\u064a\u0631:*\n\u2022 *menu* \u2014 \u0634\u0648\u0641 \u0627\u0644\u0645\u0627\u0643\u0648\u0644\n\u2022 *lista* \u2014 \u0634\u0648\u0641 \u0627\u0644\u0645\u0637\u0627\u0639\u0645\n\u2022 *pedido* \u2014 \u0634\u0648\u0641 \u0627\u0644\u0637\u0644\u0628\n\u2022 *lghi* \u2014 \u0644\u063a\u064a \u0627\u0644\u0637\u0644\u0628\n\u2022 *salam* \u2014 \u0628\u062f\u0627 \u0645\u0646 \u062c\u062f\u064a\u062f",
            "darija":  "\U0001f4cb *Chno t\u0642\u062f\u0631 dir:*\n\u2022 *menu* \u2014 shuf lmaakoul\n\u2022 *lista* \u2014 shuf restaurants\n\u2022 *pedido* \u2014 shuf lcommande\n\u2022 *lghi* \u2014 lghi lcommande\n\u2022 *salam* \u2014 bda mn jdid",
            "french":  "\U0001f4cb *Commandes disponibles:*\n\u2022 *menu* \u2014 voir la nourriture\n\u2022 *lista* \u2014 voir les restaurants\n\u2022 *pedido* \u2014 voir votre commande\n\u2022 *lghi* \u2014 annuler la commande\n\u2022 *salam* \u2014 recommencer",
            "spanish": "\U0001f4cb *Comandos disponibles:*\n\u2022 *menu* \u2014 ver la comida\n\u2022 *lista* \u2014 ver restaurantes\n\u2022 *pedido* \u2014 ver tu pedido\n\u2022 *lghi* \u2014 cancelar pedido\n\u2022 *salam* \u2014 empezar de nuevo",
            "english": "\U0001f4cb *Available commands:*\n\u2022 *menu* \u2014 see the food\n\u2022 *lista* \u2014 see restaurants\n\u2022 *pedido* \u2014 see your order\n\u2022 *lghi* \u2014 cancel order\n\u2022 *salam* \u2014 start over",
            "german":  "\U0001f4cb *Verf\u00fcgbare Befehle:*\n\u2022 *menu* \u2014 Essen ansehen\n\u2022 *lista* \u2014 Restaurants ansehen\n\u2022 *pedido* \u2014 Bestellung ansehen\n\u2022 *lghi* \u2014 Bestellung stornieren\n\u2022 *salam* \u2014 neu beginnen",
            "turkish": "\U0001f4cb *Mevcut komutlar:*\n\u2022 *menu* \u2014 yemekleri g\u00f6r\n\u2022 *lista* \u2014 restoranlar\u0131 g\u00f6r\n\u2022 *pedido* \u2014 sipari\u015fini g\u00f6r\n\u2022 *lghi* \u2014 sipari\u015fi iptal et\n\u2022 *salam* \u2014 ba\u015ftan ba\u015fla",
        },
        "unknown": {
            "arabic":  "\U0001f605 \u0645\u0627 \u0641\u0647\u0645\u062a\u0643\u0634\nKteb *menu* \u0628\u0627\u0634 \u062a\u0634\u0648\u0641 \u0627\u0644\u0645\u0627\u0643\u0648\u0644\n\u0648\u0644\u0627 *ayuda* \u0628\u0627\u0634 \u062a\u0634\u0648\u0641 \u0627\u0644\u062e\u064a\u0627\u0631\u0627\u062a.",
            "darija":  "\U0001f605 Ma fhamteksh\nKteb *menu* bach tchouf lmaakoul\nWlla *ayuda* bach tchouf chno t\u0642\u062f\u0631 dir.",
            "french":  "\U0001f605 Je n'ai pas compris\nTapez *menu* pour voir les plats\nOu *ayuda* pour les options.",
            "spanish": "\U0001f605 No te entend\u00ed\nEscribe *menu* para ver los platos\nO *ayuda* para las opciones.",
            "english": "\U0001f605 I didn't understand\nType *menu* to see the food\nOr *ayuda* for options.",
            "german":  "\U0001f605 Ich habe nicht verstanden\nTippen Sie *menu* f\u00fcr das Essen\nOder *ayuda* f\u00fcr Optionen.",
            "turkish": "\U0001f605 Anlamad\u0131m\nYemekler i\u00e7in *menu* yaz\u0131n\nVeya se\u00e7enekler i\u00e7in *ayuda* yaz\u0131n.",
        },
        "cancel": {
            "arabic":  "\u2705 \u0644\u063a\u064a\u0646\u0627 \u0644\u0637\u0644\u0628. Kteb *menu* \u0625\u0645\u062a\u0649 \u0628\u063a\u064a\u062a\u064a \U0001f60a",
            "darija":  "\u2705 Lghinak lcommande. Kteb *menu* imta bghiti trj3 \U0001f60a",
            "french":  "\u2705 Commande annul\u00e9e. Tapez *menu* quand vous voulez \U0001f60a",
            "spanish": "\u2705 Pedido cancelado. Escribe *menu* cuando quieras \U0001f60a",
            "english": "\u2705 Order cancelled. Type *menu* whenever you're ready \U0001f60a",
            "german":  "\u2705 Bestellung storniert. Tippen Sie *menu* wann immer Sie m\u00f6chten \U0001f60a",
            "turkish": "\u2705 Sipari\u015f iptal edildi. Haz\u0131r oldu\u011funuzda *menu* yaz\u0131n \U0001f60a",
        },
        "confirm": {
            "arabic":  "\u2705 \u0648\u0627\u0643\u0647\u0627! \u0627\u0644\u0637\u0644\u0628 \u062f\u064a\u0627\u0644\u0643 \u0645\u0642\u064a\u062f. \u063a\u0627\u062f\u064a \u0646\u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0643 \u0642\u0631\u064a\u0628\u0627 \U0001f64f",
            "darija":  "\u2705 Wakha! L-commande dyalek m\u0642\u064a\u062f\u0629. Ghadi n\u062a\u0648\u0627\u0635\u0644 m3ak \u0642\u0631\u064a\u0628\u0627\u064b \U0001f64f",
            "french":  "\u2705 Parfait! Votre commande est enregistr\u00e9e. Nous vous contacterons bient\u00f4t \U0001f64f",
            "spanish": "\u2705 \u00a1Perfecto! Tu pedido est\u00e1 registrado. Te contactaremos pronto \U0001f64f",
            "english": "\u2705 Great! Your order is registered. We'll contact you soon \U0001f64f",
            "german":  "\u2705 Perfekt! Ihre Bestellung ist registriert. Wir melden uns bald \U0001f64f",
            "turkish": "\u2705 Harika! Sipari\u015finiz kaydedildi. Yak\u0131nda sizinle ileti\u015fime ge\u00e7ece\u011fiz \U0001f64f",
        },
        "order_ask": {
            "arabic":  "\U0001f374 \u0634\u0646\u0648 \u0628\u063a\u064a\u062a\u064a \u062a\u0637\u0644\u0628\u064a? Kteb *menu* \u0628\u0627\u0634 \u062a\u0634\u0648\u0641 \u0627\u0644\u0645\u0627\u0643\u0648\u0644.",
            "darija":  "\U0001f374 Chno bghiti t\u0637\u0644b? Kteb *menu* bach tchouf lmaakoul.",
            "french":  "\U0001f374 Que souhaitez-vous commander? Tapez *menu* pour voir les plats.",
            "spanish": "\U0001f374 \u00bfQu\u00e9 quieres pedir? Escribe *menu* para ver los platos.",
            "english": "\U0001f374 What would you like to order? Type *menu* to see the dishes.",
            "german":  "\U0001f374 Was m\u00f6chten Sie bestellen? Tippen Sie *menu* f\u00fcr die Speisen.",
            "turkish": "\U0001f374 Ne sipari\u015f etmek istersiniz? Yemekler i\u00e7in *menu* yaz\u0131n.",
        },
    }
    
    @classmethod
    def _get(cls, key: str, lang: str) -> str:
        msgs = cls.MSG.get(key, cls.MSG["unknown"])
        return msgs.get(lang, msgs.get("darija", ""))
    
    @classmethod
    async def process_message(cls, from_number: str, message_text: str) -> str:
        lang   = DarijaNLP.detect_language(message_text)
        intent = DarijaNLP.detect_intent(message_text)
        logger.info(f"[BOT] {from_number[-4:]} | lang={lang} | intent={intent} | '{message_text[:40]}'")
        
        try:
            sb = get_supabase()
            sb.table("messages").insert({
                "from_number":  from_number,
                "message_text": message_text,
                "direction":    "incoming",
                "created_at":   datetime.utcnow().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[DB] No se pudo guardar: {e}")
        
        if intent in ["greeting", "help", "cancel", "confirm"]:
            return cls._get(intent, lang)
        if intent == "order":
            return cls._get("order_ask", lang)
        if intent == "menu":
            return await cls.get_menu(from_number, lang)
        if intent == "lista":
            return await cls.get_restaurants(lang)
        if intent == "pedido":
            return await cls.get_order(from_number, lang)
        if intent == "add_item":
            return await cls.add_to_order(from_number, int(message_text.strip()), lang)
        return cls._get("unknown", lang)
    
    @classmethod
    async def get_restaurants(cls, lang: str = "darija") -> str:
        try:
            sb = get_supabase()
            res = sb.table("clients").select(
                "name,business_type,zone,google_rating,google_reviews"
            ).eq("is_active", True).order("google_reviews", desc=True).limit(10).execute()
            
            if not res.data:
                return {
                    "darija": "Ma kayn hta restaurant.", "arabic": "\u0644\u0627 \u064a\u0648\u062c\u062f \u0645\u0637\u0627\u0639\u0645.",
                    "french": "Aucun restaurant.", "spanish": "No hay restaurantes.",
                    "english": "No restaurants.", "german": "Keine Restaurants.", "turkish": "Restoran yok.",
                }.get(lang, "Ma kayn hta restaurant.")
            
            headers = {
                "darija": "\U0001f3ea *Restaurants f Tetouan:*\n",
                "arabic": "\U0001f3ea *\u0627\u0644\u0645\u0637\u0627\u0639\u0645 \u0641\u064a \u062a\u0637\u0648\u0627\u0646:*\n",
                "french": "\U0001f3ea *Restaurants a Tetouan:*\n",
                "spanish": "\U0001f3ea *Restaurantes en Tetouan:*\n",
                "english": "\U0001f3ea *Restaurants in Tetouan:*\n",
                "german": "\U0001f3ea *Restaurants in Tetouan:*\n",
                "turkish": "\U0001f3ea *Tetouan'daki Restoranlar:*\n",
            }
            lines = [headers.get(lang, headers["darija"])]
            
            emojis = {"restaurant": "\U0001f374", "cafe": "\u2615", "fast_food": "\U0001f354", "seafood": "\U0001f990"}
            for r in res.data:
                e = emojis.get(r.get("business_type", ""), "\U0001f374")
                lines.append(f"{e} *{r['name']}*")
                lines.append(f"   \U0001f4cd {r.get('zone','?')} | \u2b50 {r.get('google_rating','?')} ({r.get('google_reviews','?')})\n")
            
            footers = {
                "darija": "Kteb *menu* bach tchouf lmaakoul.",
                "arabic": "\u0627\u0643\u062a\u0628 *menu* \u0644\u0631\u0624\u064a\u0629 \u0627\u0644\u0642\u0627\u0626\u0645\u0629.",
                "french": "Tapez *menu* pour voir le menu.",
                "spanish": "Escribe *menu* para ver el menu.",
                "english": "Type *menu* to see the menu.",
                "german": "Tippen Sie *menu* fur die Speisekarte.",
                "turkish": "Menu icin *menu* yazin.",
            }
            lines.append(footers.get(lang, footers["darija"]))
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error restaurantes: {e}")
            return "Error. 3afak 3awd mn b3d / Please try again."
    
    @classmethod
    async def get_menu(cls, from_number: str, lang: str = "darija") -> str:
        try:
            sb = get_supabase()
            cr = sb.table("clients").select("id,name").eq(
                "is_active", True
            ).order("google_reviews", desc=True).limit(1).execute()
            
            if not cr.data:
                return "Ma kayn hta menu / No menu available."
            
            client = cr.data[0]
            mr = sb.table("menu_items").select("*").eq(
                "client_id", client["id"]
            ).eq("is_available", True).execute()
            
            if not mr.data:
                return f"Menu dyal *{client['name']}* ma kaynsh daba."
            
            headers = {
                "darija":  f"\U0001f4cb *Menu dyal {client['name']}:*\n",
                "arabic":  f"\U0001f4cb *\u0642\u0627\u0626\u0645\u0629 {client['name']}:*\n",
                "french":  f"\U0001f4cb *Menu de {client['name']}:*\n",
                "spanish": f"\U0001f4cb *Menu de {client['name']}:*\n",
                "english": f"\U0001f4cb *Menu of {client['name']}:*\n",
                "german":  f"\U0001f4cb *Menu von {client['name']}:*\n",
                "turkish": f"\U0001f4cb *{client['name']} Menusu:*\n",
            }
            lines = [headers.get(lang, headers["darija"])]
            
            current_cat = ""
            for item in mr.data:
                cat = item.get("category", "")
                if cat != current_cat:
                    current_cat = cat
                    lines.append(f"\n*{cat.upper()}*")
                lines.append(f"  \u2022 *{item['dish_name']}* \u2014 {item['price']} MAD")
                if item.get("description"):
                    lines.append(f"    _{item['description']}_")
            
            footers = {
                "darija":  "\n\u270d\ufe0f Kteb esm lplat bach t\u0637\u0644b.",
                "arabic":  "\n\u270d\ufe0f \u0627\u0643\u062a\u0628 \u0627\u0633\u0645 \u0627\u0644\u0637\u0628\u0642 \u0644\u0644\u0637\u0644\u0628.",
                "french":  "\n\u270d\ufe0f Tapez le nom du plat pour commander.",
                "spanish": "\n\u270d\ufe0f Escribe el nombre del plato para pedir.",
                "english": "\n\u270d\ufe0f Type the dish name to order.",
                "german":  "\n\u270d\ufe0f Tippen Sie den Gerichtnamen zur Bestellung.",
                "turkish": "\n\u270d\ufe0f Siparis icin yemek adini yazin.",
            }
            lines.append(footers.get(lang, footers["darija"]))
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[DB] Error menu: {e}")
            return "Error al cargar el menu. 3afak 3awd mn b3d."
    
    @classmethod
    async def get_order(cls, from_number: str, lang: str = "darija") -> str:
        msgs = {
            "darija":  "\U0001f6d2 Ma 3andeksh commande active.\nKteb *menu* bach tbda.",
            "arabic":  "\U0001f6d2 \u0644\u064a\u0633 \u0644\u062f\u064a\u0643 \u0637\u0644\u0628 \u0646\u0634\u0637.\n\u0627\u0643\u062a\u0628 *menu* \u0644\u0644\u0628\u062f\u0621.",
            "french":  "\U0001f6d2 Vous n'avez pas de commande active.\nTapez *menu* pour commencer.",
            "spanish": "\U0001f6d2 No tienes pedido activo.\nEscribe *menu* para empezar.",
            "english": "\U0001f6d2 You have no active order.\nType *menu* to start.",
            "german":  "\U0001f6d2 Sie haben keine aktive Bestellung.\nTippen Sie *menu* zum Starten.",
            "turkish": "\U0001f6d2 Aktif siparisiniz yok.\nBaslamak icin *menu* yazin.",
        }
        return msgs.get(lang, msgs["darija"])
    
    @classmethod
    async def add_to_order(cls, from_number: str, item_number: int, lang: str = "darija") -> str:
        msgs = {
            "darija":  f"\u2705 Plat #{item_number} tzad l lcommande dyalek.\nKteb *pedido* bach tchouf.",
            "arabic":  f"\u2705 \u0627\u0644\u0637\u0628\u0642 #{item_number} \u0623\u0636\u064a\u0641 \u0644\u0637\u0644\u0628\u0643.\n\u0627\u0643\u062a\u0628 *pedido* \u0644\u0644\u0639\u0631\u0636.",
            "french":  f"\u2705 Plat #{item_number} ajoute a votre commande.\nTapez *pedido* pour voir.",
            "spanish": f"\u2705 Plato #{item_number} anadido a tu pedido.\nEscribe *pedido* para ver.",
            "english": f"\u2705 Dish #{item_number} added to your order.\nType *pedido* to see it.",
            "german":  f"\u2705 Gericht #{item_number} hinzugefugt.\nTippen Sie *pedido* zum Ansehen.",
            "turkish": f"\u2705 Yemek #{item_number} eklendi.\nGormek icin *pedido* yazin.",
        }
        return msgs.get(lang, msgs["darija"])

# ══════════════════════════════════════════════════════════
# WHATSAPP SERVICE (SIN CAMBIOS)
# ══════════════════════════════════════════════════════════
class WhatsAppService:
    BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"
    
    @classmethod
    async def send_text(cls, to: str, message: str) -> Dict:
        if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
            logger.error("[WA] Faltan WHATSAPP_TOKEN o PHONE_NUMBER_ID")
            return {"error": "Config incompleta"}
        
        url = f"{cls.BASE_URL}/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.post(url, headers=headers, json=payload)
                data = r.json()
                if r.status_code == 200:
                    logger.info(f"[WA] Enviado a ...{to[-4:]}: {message[:50]}")
                else:
                    logger.error(f"[WA] Error {r.status_code}: {data}")
                return data
            except Exception as e:
                logger.error(f"[WA] Excepcion: {e}")
                return {"error": str(e)}

# ══════════════════════════════════════════════════════════
# LIFESPAN (SIN CAMBIOS)
# ══════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[STARTUP] Orquestrator ISA v2.1 iniciando...")
    try:
        sb = get_supabase()
        res = sb.table("clients").select("count", count="exact").limit(1).execute()
        logger.info(f"[STARTUP] Supabase OK. Clients: {res.count}")
    except Exception as e:
        logger.error(f"[STARTUP] Supabase error: {e}")
    yield
    logger.info("[SHUTDOWN] Orquestrator ISA detenido.")

# ══════════════════════════════════════════════════════════
# FASTAPI APP
# ══════════════════════════════════════════════════════════
app = FastAPI(
    title="Orquestrator ISA",
    description="WhatsApp Business API 7 idiomas Tetouan Marruecos",
    version="2.1.0",
    lifespan=lifespan,
)

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Orquestrator ISA",
        "version": "2.1.0",
        "languages": ["darija", "arabic", "french", "spanish", "english", "german", "turkish"],
        "webhook": f"https://chatcommerce-bot.onrender.com{WEBHOOK_PREFIX}",
    }

@app.get("/health")
async def health():
    status = {
        "status": "healthy",
        "supabase": False,
        "whatsapp_token": bool(WHATSAPP_TOKEN),
        "phone_number_id": bool(PHONE_NUMBER_ID),
        "languages": 7,
    }
    try:
        sb = get_supabase()
        sb.table("clients").select("count", count="exact").limit(1).execute()
        status["supabase"] = True
    except Exception:
        pass
    return status

@app.get(WEBHOOK_PREFIX)
async def webhook_verify(request: Request):
    params             = request.query_params
    hub_mode           = params.get("hub.mode")
    hub_verify_token   = params.get("hub.verify_token")
    hub_challenge      = params.get("hub.challenge")
    
    logger.info(f"[WEBHOOK GET] mode={hub_mode} token={hub_verify_token} challenge={hub_challenge}")
    
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("[WEBHOOK GET] Verificacion exitosa")
        return Response(content=hub_challenge, media_type="text/plain")
    
    logger.warning(f"[WEBHOOK GET] Recibido={hub_verify_token} Esperado={VERIFY_TOKEN}")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post(WEBHOOK_PREFIX)
async def webhook_post(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        logger.info(f"[WEBHOOK POST] {json.dumps(body, ensure_ascii=False)[:200]}")
    except Exception as e:
        logger.error(f"[WEBHOOK POST] JSON error: {e}")
        return JSONResponse({"status": "error"}, status_code=400)
    
    background_tasks.add_task(process_payload, body)
    return JSONResponse({"status": "ok"}, status_code=200)

async def process_payload(body: Dict[str, Any]):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    from_number = msg.get("from")
                    msg_type = msg.get("type")
                    
                    logger.info(f"[MSG] De={from_number} Tipo={msg_type}")
                    
                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "")
                        response = await BotLogic.process_message(from_number, text)
                        await WhatsAppService.send_text(from_number, response)
                    else:
                        await WhatsAppService.send_text(
                            from_number,
                            "Tslmt risaltek. Daba kan3alej ghi n-nsos.\nKteb *ayuda* bach tchouf chno tقدر dir."
                        )
    except Exception as e:
        logger.error(f"[PAYLOAD] Error: {e}", exc_info=True)

# ══════════════════════════════════════════════════════════
# API ENDPOINTS CORREGIDOS
# ══════════════════════════════════════════════════════════

@app.get("/api/clients")
async def list_clients():
    try:
        sb = get_supabase()
        res = sb.table("clients").select("*").eq("is_active", True).order(
            "google_reviews", desc=True
        ).execute()
        return {"clients": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients")
async def create_client(client: ClientCreate):
    try:
        sb = get_supabase()
        data = {
            "name": client.nombre,
            "owner_phone": client.telefono,
            "language": client.idioma,
            "business_type": client.tipo_negocio,
            "zone": client.zona,
            "plan": client.plan,
            "is_active": True,
            "total_messages": 0,
            "total_orders": 0,
            "whatsapp_status": "contactar",
            "trial_ends_at": (datetime.utcnow() + timedelta(days=20)).date().isoformat(),
        }
        res = sb.table("clients").insert(data).execute()
        return {"client": res.data[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/menu/{client_id}")
async def get_menu(client_id: str):
    try:
        sb = get_supabase()
        res = sb.table("menu_items").select("*").eq(
            "client_id", client_id
        ).eq("is_available", True).execute()
        return {"menu_items": res.data, "count": len(res.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/menu")
async def create_menu_item(item: MenuItemCreate):
    try:
        sb = get_supabase()
        res = sb.table("menu_items").insert({
            "client_id": item.menu_id,
            "category": item.categoria,
            "dish_name": item.nombre,
            "description": item.descripcion,
            "price": item.precio,
            "is_available": item.disponible,
        }).execute()
        return {"menu_item": res.data[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
async def get_stats():
    try:
        sb = get_supabase()
        clients  = sb.table("clients").select("count", count="exact").execute()
        menu     = sb.table("menu_items").select("count", count="exact").execute()
        messages = sb.table("messages").select("count", count="exact").execute()
        return {
            "clients": clients.count,
            "menu_items": menu.count,
            "messages": messages.count,
            "whatsapp_configured": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
            "languages_supported": 7,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
