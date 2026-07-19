import os
import io
import base64
import qrcode
import crcmod
import unicodedata
import mercadopago
from typing import Annotated
from fastapi import FastAPI, Request, Form, status, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from supabase import create_client, Client
from fastapi.staticfiles import StaticFiles # <-- ADICIONE ESTA LINHA

app = FastAPI()
templates = Jinja2Templates(directory="templates")


# Configurações do Mercado Pago e Supabase via Variáveis de Ambiente
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- SUA FUNÇÃO DE LIMPEZA IDENTICA ---
def limpar_texto(texto):
    # Remove acentos, caracteres especiais e força letras maiúsculas
    return "".join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    ).upper().replace('$', '').replace('@', '@') # mantém o @ se for e-mail

# --- SUA FUNÇÃO DO PIX QUE DEU CERTO (Ajustada apenas para não quebrar a variável) ---
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
    
    # Procure a parte que adiciona o valor (ID 54) e altere para:
    transaction_amount = ""
    if valor > 0:
        valor_str = f"{valor:.2f}"
        transaction_amount = f"54{len(valor_str):02d}{valor_str}"
    
    country_code = "5802BR"
    
    merchant_name = f"59{len(nome):02d}{nome}"
    merchant_city = f"60{len(cidade):02d}{cidade}"
    
    # Bloco do TXID formatado rigidamente
    additional_data = f"05{len(txid):02d}{txid}"
    additional_data_template = f"62{len(additional_data):02d}{additional_data}"
    
    # Concatenação da payload base exatamente como o seu código estruturou
    payload = (
        payload_format_indicator +
        merchant_account_len +
        merchant_category_code +
        transaction_currency +
        transaction_amount +  # Adicionado de forma segura
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
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M, # Nível médio evita erros de leitura
        box_size=10,
        border=4,
    )
    qr.add_data(payload_pix)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"

# --- ROTAS DO SISTEMA FREEMIUM ---

@app.get("/", response_class=HTMLResponse)
async def pagina_inicial(request: Request):
    resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
    return templates.TemplateResponse("index.html", {"request": request, "historico": resposta.data})

# ROTA PRINCIPAL CORRIGIDA PARA LER A LISTA DO SUPABASE
@app.post("/", response_class=HTMLResponse)
async def criar_qrcode(
    request: Request,
    chave: Annotated[str | None, Form()] = None,
    nome: Annotated[str | None, Form()] = None,
    cidade: Annotated[str | None, Form()] = None,
    valor: Annotated[float | None, Form()] = None,
    email_cliente: Annotated[str | None, Form()] = None
):
    params = dict(request.query_params)
    if params.get("data.id") or params.get("id"):
        id_pagamento = params.get("data.id") or params.get("id")
        if str(id_pagamento) == "123456":
            return Response(status_code=status.HTTP_200_OK)
        return await webhook_mercadopago(request, Response(), id=id_pagamento, topic="payment")

    if not all([chave, nome, cidade, email_cliente]) or valor is None:
        resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
        return templates.TemplateResponse("index.html", {
            "request": request, "historico": resposta.data,
            "erro_pagamento": "Erro no envio do formulário. Preencha todos os campos obrigatórios."
        })

    email_verificar = email_cliente.strip().lower()
    user_query = supabase.table("usuarios_pagos").select("*").eq("email", email_verificar).execute()
    
    if not user_query.data or len(user_query.data) == 0:
        user_insert = supabase.table("usuarios_pagos").insert({"email": email_verificar, "creditos": 3}).execute()
        user_data = user_insert.data[0] # CORREÇÃO: Acessa o primeiro item da lista criada
    else:
        user_data = user_query.data[0] # CORREÇÃO: Acessa o primeiro item da lista encontrada

    if user_data["creditos"] <= 0:
        resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
        return templates.TemplateResponse("index.html", {
            "request": request, "historico": resposta.data,
            "erro_pagamento": "Seus 3 créditos de teste acabaram. Digite seu e-mail abaixo para adquirir mais créditos.",
            "email_bloqueado": email_verificar
        })

    novos_creditos = user_data["creditos"] - 1
    supabase.table("usuarios_pagos").update({"creditos": novos_creditos}).eq("email", email_verificar).execute()

    payload_pix = gerar_payload_pix_estrito(chave, nome, cidade, valor)
    qrcode_base64 = gerar_base64_qrcode(payload_pix)
    
    supabase.table("qrcodes").insert({"chave": chave, "nome": nome, "cidade": cidade, "valor": valor, "payload_pix": payload_pix, "image_url": qrcode_base64}).execute()
    resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
    
    return templates.TemplateResponse("index.html", {
        "request": request, "qrcode_gerado": qrcode_base64, "payload_final": payload_pix, "historico": resposta.data, 
        "creditos_restantes": novos_creditos, "email_usado": email_verificar
    })

   

@app.post("/comprar-creditos", response_class=HTMLResponse)
async def comprar_creditos(request: Request, email_compra: Annotated[str, Form()]):
    email_limpo = email_compra.strip().lower()
    
    # Cria cobrança de R$ 19,90 no Mercado Pago
    payment_data = {
        "transaction_amount": 19.90,
        "description": "Recarga 50 Créditos - QR Pix Pro",
        "payment_method_id": "pix",
        "payer": {"email": email_limpo}
    }
    
    payment_response = sdk.payment().create(payment_data)
    payment = payment_response["response"]
    
    pix_copia_cola = payment["point_of_interaction"]["transaction_data"]["qr_code"]
    pix_qr_base64 = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
    
    resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
    return templates.TemplateResponse("index.html", {
        "request": request, "historico": resposta.data,
        "checkout_pix": pix_copia_cola, "checkout_qr": f"data:image/png;base64,{pix_qr_base64}", "email_solicitado": email_limpo
    })

# WEBHOOK CORRIGIDO PARA LER LISTA DO SUPABASE NO INDEX [0]
@app.post("/webhook/mercadopago")
async def webhook_mercadopago(
    request: Request, 
    response: Response,
    id: str | None = None,
    topic: str | None = None
):
    id_pagamento = None
    params = dict(request.query_params)
    if params.get("type") == "payment" and params.get("data.id"):
        id_pagamento = params.get("data.id")
    elif topic == "payment" and id:
        id_pagamento = id

    if not id_pagamento:
        try:
            payload = await request.json()
            if payload.get("type") == "payment" or payload.get("action") in ["payment.created", "payment.updated"]:
                id_pagamento = payload.get("data", {}).get("id") or payload.get("id")
        except Exception:
            pass

    if id_pagamento:
        if str(id_pagamento) == "123456":
            return Response(status_code=status.HTTP_200_OK)

        try:
            pagamento_response = sdk.payment().get(id_pagamento)
            pagamento_info = pagamento_response.get("response", {})
            
            if pagamento_info.get("status") == "approved":
                email_pagador = pagamento_info["payer"]["email"].lower()
                existe = supabase.table("usuarios_pagos").select("*").eq("email", email_pagador).execute()
                
                # CORREÇÃO CRÍTICA AQUI: Acessa o índice [0] da lista
                if existe.data and len(existe.data) > 0:
                    usuario_atual = existe.data[0] # Índice [0] adicionado
                    creditos_atuais = usuario_atual["creditos"] + 50
                    supabase.table("usuarios_pagos").update({"creditos": creditos_atuais}).eq("email", email_pagador).execute()
                else:
                    supabase.table("usuarios_pagos").insert({"email": email_pagador, "creditos": 50}).execute()
                    
        except Exception as e:
            print(f"Erro interno no processamento do webhook: {e}")
            return Response(status_code=status.HTTP_200_OK)
                
    return Response(status_code=status.HTTP_200_OK)

# ROTA ADICIONAL: API rápida para o HTML checar o saldo sem precisar dar refresh na página
@app.get("/checar-creditos")
async def checar_creditos(email: str):
    user_query = supabase.table("usuarios_pagos").select("creditos").eq("email", email.strip().lower()).execute()
    if user_query.data and len(user_query.data) > 0:
        return {"creditos": user_query.data[0]["creditos"]}
    return {"creditos": 0}
