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
    ).upper().replace('$', '').replace('@', '@')

def gerar_payload_pix_estrito(chave, nome, cidade, valor, txid="***"):
    # Limpa os campos conforme sua regra original
    nome = limpar_texto(nome)[:25]
    cidade = limpar_texto(cidade)[:15]
    txid = limpar_texto(txid)[:25]
    
    # 00: Indicador do formato (Fixo)
    payload = "000201"
    
    # 26: Dados da Conta do Recebedor (Chave Pix)
    # Aqui o tamanho do sub-bloco da chave (01) deve ser calculado dinamicamente
    gui = "0014BR.GOV.BCB.PIX"
    sub_bloco_chave = f"01{len(chave):02d}{chave}"
    merchant_account = gui + sub_bloco_chave
    
    # Monta o ID 26 com o tamanho correto de toda a string de conta
    payload += f"26{len(merchant_account):02d}{merchant_account}"
    
    # 52: Merchant Category Code (Código de Categoria - Fixo)
    payload += "52040000"
    
    # 53: Transaction Currency (Moeda: 986 para Real - Fixo)
    payload += "5303986"
    
    # 54: Transaction Amount (Valor - Opcional se for zero)
    if valor > 0:
        valor_str = f"{valor:.2f}"
        payload += f"54{len(valor_str):02d}{valor_str}"
    
    # 58: Country Code (Código do País: BR - Fixo)
    payload += "5802BR"
    
    # 59: Merchant Name (Nome do Beneficiário)
    payload += f"59{len(nome):02d}{nome}"
    
    # 60: Merchant City (Cidade do Beneficiário)
    payload += f"60{len(cidade):02d}{cidade}"
    
    # 62: Additional Data Field Template (Contém o TXID)
    additional_data = f"05{len(txid):02d}{txid}"
    payload += f"62{len(additional_data):02d}{additional_data}"
    
    # 63: Indicador de início do cálculo de CRC (Fixo)
    payload += "6304"
    
    # Cálculo do CRC16 CCITT exato sobre a string montada
    crc16 = crcmod.mkCrcFun(poly=0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    crc_code = hex(crc16(payload.encode('utf-8')))[2:].upper().zfill(4)
    
    return payload + crc_code

def gerar_base64_qrcode(payload_pix: str) -> str:
    """Gera a imagem na memória com o nível médio de correção solicitado"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
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
    # 1. Gera o payload Pix estrito corrigido
    payload_pix = gerar_payload_pix_estrito(chave, nome, cidade, valor)
    
    # 2. Gera o QR Code em formato de texto Base64 para exibição direta
    qrcode_base64 = gerar_base64_qrcode(payload_pix)
    
    # 3. Armazena a transação no banco do Supabase
    dados_banco = {
        "chave": chave,
        "nome": nome,
        "cidade": cidade,
        "valor": valor,
        "payload_pix": payload_pix,
        "image_url": qrcode_base64
    }
    supabase.table("qrcodes").insert(dados_banco).execute()
    
    # 4. Busca novamente o histórico atualizado
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