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
    if not texto:
        return ""
    # Remove acentos, força maiúsculas e remove caracteres especiais
    return "".join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    ).upper().strip()

def gerar_payload_pix_estrito(chave, nome, city, valor, txid="***"):
    chave_limpa = chave.strip()
    
    # E-mails precisam obrigatoriamente estar em letras minúsculas
    if "@" in chave_limpa:
        chave_limpa = chave_limpa.lower()
    else:
        # Adiciona o DDI +55 para números de celular de 10 ou 11 dígitos
        tam = len(chave_limpa)
        if chave_limpa.isdigit() and (tam == 10 or tam == 11):
            chave_limpa = f"+55{chave_limpa}"
        elif chave_limpa.isdigit() and tam == 13 and not chave_limpa.startswith("+"):
            chave_limpa = f"+{chave_limpa}"

    nome = limpar_texto(nome)[:25]
    cidade = limpar_texto(city)[:15]
    txid = limpar_texto(txid)[:25]
    
    if not txid or txid == "***":
        txid = "***"

    # 00: Indicador do formato da Payload
    payload = "000201"
    
    # 26: Merchant Account Information (Chave Pix)
    gui = "0014BR.GOV.BCB.PIX"
    sub_bloco_chave = f"01{len(chave_limpa):02d}{chave_limpa}"
    merchant_account = gui + sub_bloco_chave
    payload += f"26{len(merchant_account):02d}{merchant_account}"
    
    # 52: Merchant Category Code (Fixo)
    payload += "52040000"
    
    # 53: Transaction Currency (Fixo: 986 para Real)
    payload += "5303986"
    
    # 54: Transaction Amount (O Banco do Brasil exige o valor explícito)
    valor_str = f"{valor:.2f}"
    payload += f"54{len(valor_str):02d}{valor_str}"
    
    # 58: Country Code (Fixo: BR)
    payload += "5802BR"
    
    # 59: Merchant Name
    payload += f"59{len(nome):02d}{nome}"
    
    # 60: Merchant City
    payload += f"60{len(cidade):02d}{cidade}"
    
    # 62: Additional Data Field Template (TXID)
    additional_data = f"05{len(txid):02d}{txid}"
    payload += f"62{len(additional_data):02d}{additional_data}"
    
    # 63: Indicador do CRC
    payload += "6304"
    
    # Cálculo matemático do CRC16 CCITT
    crc16 = crcmod.mkCrcFun(poly=0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    crc_code = hex(crc16(payload.encode('utf-8')))[2:].upper().zfill(4)
    
    return payload + crc_code

def gerar_base64_qrcode(payload_pix: str) -> str:
    """Gera o QR Code expandindo a matriz dinamicamente para evitar pontos esmagados"""
    qr = qrcode.QRCode(
        version=None,  # Deixar None faz o qrcode escolher o tamanho ideal baseado no texto
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,      # Margem branca necessária para sensores exigentes como o do BB
    )
    qr.add_data(payload_pix)
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
