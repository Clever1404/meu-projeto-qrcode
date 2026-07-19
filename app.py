import os
import io
import base64
import qrcode
import crcmod
import unicodedata
from typing import Annotated
from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from supabase import create_client, Client

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Configuração do Supabase via Variáveis de Ambiente
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def limpar_texto(texto):
    # Remove acentos, caracteres especiais e força letras maiúsculas
    return "".join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    ).upper().replace('$', '').replace('@', '@') # mantém o @ se for e-mail

def gerar_payload_pix_estrito(chave, nome, cidade, valor, txid="***"):
    nome = limpar_texto(nome)[:25] # Limite de caracteres padrão EMV
    cidade = limpar_texto(cidade)[:15]
    txid = limpar_texto(txid)[:25]
    
    payload_format_indicator = "000201"
    
    # --- AJUSTE CRÍTICO: Cálculo dinâmico do Merchant Account para o BB ---
    gui = "0014BR.GOV.BCB.PIX"
    sub_bloco_chave = f"01{len(chave):02d}{chave}"
    merchant_account = gui + sub_bloco_chave
    merchant_account_len = f"26{len(merchant_account):02d}{merchant_account}"
    # ----------------------------------------------------------------------
    
    merchant_category_code = "52040000"
    transaction_currency = "5303986"
    
    # Formata valor com duas casas decimais e mede o tamanho dinamicamente
    #valor_str = f"{valor:.2f}"
    #transaction_amount = f"54{len(valor_str):02d}{valor_str}"

    # Procure a parte que adiciona o valor (ID 54) e altere para:
    if valor > 0:
        valor_str = f"{valor:.2f}"
        payload += f"54{len(valor_str):02}{valor_str}"
    
    country_code = "5802BR"
    
    merchant_name = f"59{len(nome):02d}{nome}"
    merchant_city = f"60{len(cidade):02d}{cidade}"
    
    # Bloco do TXID formatado rigidamente
    additional_data = f"05{len(txid):02d}{txid}"
    additional_data_template = f"62{len(additional_data):02d}{additional_data}"
    
    # Concatenação da payload base
    payload = (
        payload_format_indicator +
        merchant_account_len +
        merchant_category_code +
        transaction_currency +
        country_code +
        merchant_name +
        merchant_city +
        additional_data_template +
        "6304"
    )
    
    # Cálculo do CRC16 CCITT
    crc16 = crcmod.mkCrcFun(poly=0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    crc_code = hex(crc16(payload.encode('utf-8')))[2:].upper().zfill(4)
    
    return payload + crc_code

def gerar_base64_qrcode(payload_pix: str) -> str:
    """Gera o QR Code com o nível de erro padrão, permitindo leitura em qualquer tamanho"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload_pix)  # Removemos o modo restrito alfanumérico para evitar o ValueError
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"

@app.get("/", response_class=HTMLResponse)
async def pagina_inicial(request: Request):
    resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(10).execute()
    historico = resposta.data
    return templates.TemplateResponse("index.html", {"request": request, "historico": historico})

@app.post("/", response_class=HTMLResponse)
async def criar_qrcode(
    request: Request,
    chave: Annotated[str, Form()],
    nome: Annotated[str, Form()],
    cidade: Annotated[str, Form()],
    valor: Annotated[float, Form()]
):
    payload_pix = gerar_payload_pix_estrito(chave, nome, cidade, valor)
    qrcode_base64 = gerar_base64_qrcode(payload_pix)
    
    dados_banco = {
        "chave": chave,
        "nome": nome,
        "cidade": cidade,
        "valor": valor,
        "payload_pix": payload_pix,
        "image_url": qrcode_base64
    }
    supabase.table("qrcodes").insert(dados_banco).execute()
    
    resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(10).execute()
    historico = resposta.data
    
    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request, 
            "qrcode_gerado": qrcode_base64, 
            "payload_final": payload_pix, 
            "historico": historico
        }
    )