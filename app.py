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
from fastapi.responses import PlainTextResponse
import resend 

app = FastAPI()
templates = Jinja2Templates(directory="templates")
# Obtém o caminho absoluto correto da pasta de templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Configurações do Mercado Pago e Supabase via Variáveis de Ambiente
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configura a API Key do Resend obtida do ambiente
resend.api_key = os.getenv("RESEND_API_KEY")


@app.post("/contato", response_class=HTMLResponse)
async def enviar_contato(
    request: Request, 
    nome: str = Form(...), 
    email: str = Form(...), 
    mensagem: str = Form(...)
):
    # 1. Salva a mensagem no Supabase
    supabase.table("contatos").insert({
        "nome": nome,
        "email": email,
        "mensagem": mensagem
    }).execute()
    
    # 2. Envia a notificação por e-mail via Resend
    try:
        params = {
            "from": "onboarding@resend.dev",  # Use este se for conta de testes, ou seu email verificado (ex: contato@seu-dominio.com)
            "to": ["seu-email@gmail.com"],     # Substitua pelo e-mail onde você deseja RECEBER o aviso
            "subject": f"Novo contato do site: {nome}",
            "html": f"""
                <h3>Nova Mensagem Recebida no Site</h3>
                <p><strong>Nome:</strong> {nome}</p>
                <p><strong>E-mail:</strong> {email}</p>
                <p><strong>Mensagem:</strong></p>
                <p style="background: #f4f4f4; padding: 10px; border-left: 4px solid #0070f3;">{mensagem}</p>
            """
        }
        resend.Emails.send(params)
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        # Mesmo se o e-mail falhar, o código continua para não travar a experiência do usuário
    
    return templates.TemplateResponse("contato.html", {"request": request, "sucesso": True})


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
        version=None,
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
    
    # O prefixo 'data:image/png;base64,' é obrigatório para o botão "download" funcionar
    return f"data:image/png;base64,{img_str}"


# --- ROTAS DO SISTEMA FREEMIUM ---

@app.get("/", response_class=HTMLResponse)
async def pagina_inicial(request: Request):
    resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
    return templates.TemplateResponse("index.html", {"request": request, "historico": resposta.data})


# ROTA PRINCIPAL CORRIGIDA E ALINHADA COM O RETORNO DE ARRAYS DO SUPABASE
@app.post("/", response_class=HTMLResponse)
async def criar_qrcode(
    request: Request,
    chave: Annotated[str | None, Form()] = None,
    nome: Annotated[str | None, Form()] = None,
    cidade: Annotated[str | None, Form()] = None,
    valor: Annotated[float | None, Form()] = None,
    email_cliente: Annotated[str | None, Form()] = None
):
    # Interceptador para notificações do Mercado Pago direcionadas à raiz
    params = dict(request.query_params)
    if params.get("data.id") or params.get("id"):
        id_pagamento = params.get("data.id") or params.get("id")
        if str(id_pagamento) == "123456":
            return Response(status_code=status.HTTP_200_OK)
        return await webhook_mercadopago(request, Response(), id=id_pagamento, topic="payment")

    # Validação do formulário de geração de QR Code
    if not all([chave, nome, cidade, email_cliente]) or valor is None:
        resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
        return templates.TemplateResponse("index.html", {
            "request": request, "historico": resposta.data,
            "erro_pagamento": "Erro no envio do formulário. Preencha todos os campos obrigatórios."
        })

    email_verificar = email_cliente.strip().lower()
    
    # 1. Consulta o saldo do usuário no Supabase
    user_query = supabase.table("usuarios_pagos").select("*").eq("email", email_verificar).execute()
    
    # --- CORREÇÃO DO ÍNDICE [0] DA LISTA DO SUPABASE ---
    if not user_query.data or len(user_query.data) == 0:
        # Se for o primeiro acesso absoluto do e-mail, cria com 3 créditos grátis
        user_insert = supabase.table("usuarios_pagos").insert({"email": email_verificar, "creditos": 3}).execute()
        user_data = user_insert.data[0] # Pega o primeiro item da lista criada
    else:
        user_data = user_query.data[0] # Pega o primeiro item da lista encontrada

    # 2. Bloqueia se o saldo for menor ou igual a zero (agora com leitura garantida)
    if int(user_data["creditos"]) <= 0:
        resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
        return templates.TemplateResponse("index.html", {
            "request": request, "historico": resposta.data,
            "erro_pagamento": "Seus créditos acabaram. Realize uma recarga para continuar gerando QR Codes homologados.",
            "email_bloqueado": email_verificar,
            "creditos_atuais": 0
        })

    # 3. Se tiver créditos, deduz 1 e segue com a geração do QR Code do Banco do Brasil
    novos_creditos = int(user_data["creditos"]) - 1
    supabase.table("usuarios_pagos").update({"creditos": novos_creditos}).eq("email", email_verificar).execute()

    # Executa a sua lógica estrita homologada que deu certo no BB
    payload_pix = gerar_payload_pix_estrito(chave, nome, cidade, valor)
    qrcode_base64 = gerar_base64_qrcode(payload_pix)
    
    # Salva o histórico geral de QR Codes gerados
    supabase.table("qrcodes").insert({
        "chave": chave, "nome": nome, "cidade": cidade, "valor": valor, 
        "payload_pix": payload_pix, "image_url": qrcode_base64
    }).execute()
    
    resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
    
    return templates.TemplateResponse("index.html", {
        "request": request, "qrcode_gerado": qrcode_base64, "payload_final": payload_pix, "historico": resposta.data, 
        "creditos_restantes": novos_creditos, "email_usado": email_verificar, "creditos_atuais": novos_creditos
    })

   

# ROTA DE COMPRA ATUALIZADA (Garante o e-mail na referência externa)
@app.post("/comprar-creditos", response_class=HTMLResponse)
async def comprar_creditos(
    request: Request, 
    email_compra: Annotated[str | None, Form()] = None,
    email_cliente: Annotated[str | None, Form()] = None
):
    # Captura o e-mail exato digitado na caixinha pelo cliente
    email_final = email_compra or email_cliente
    
    if not email_final:
        resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
        return templates.TemplateResponse("index.html", {
            "request": request, "historico": resposta.data,
            "erro_pagamento": "Por favor, informe um e-mail válido para realizar a recarga de créditos."
        })
        
    email_limpo = email_final.strip().lower()
    
    payment_data = {
        "transaction_amount": 19.90,
        "description": "Recarga 50 Créditos - QR Pix Pro",
        "payment_method_id": "pix",
        "external_reference": email_limpo, # <--- CRÍTICO: Guarda o e-mail real aqui!
        "payer": {"email": email_limpo}
    }
    
    try:
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response["response"]
        
        pix_copia_cola = payment["point_of_interaction"]["transaction_data"]["qr_code"]
        pix_qr_base64 = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        
        resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
        return templates.TemplateResponse("index.html", {
            "request": request, "historico": resposta.data,
            "checkout_pix": pix_copia_cola, "checkout_qr": f"data:image/png;base64,{pix_qr_base64}", "email_solicitado": email_limpo
        })
    except Exception as e:
        print(f"Erro ao gerar cobrança no Mercado Pago: {e}")
        resposta = supabase.table("qrcodes").select("*").order("created_at", desc=True).limit(5).execute()
        return templates.TemplateResponse("index.html", {
            "request": request, "historico": resposta.data,
            "erro_pagamento": "Ocorreu um erro de comunicação com o Mercado Pago. Tente novamente."
        })



# WEBHOOK ATUALIZADO (Lê a referência externa para salvar o e-mail certo)
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
                # --- BUSCA O E-MAIL REAL DA REFERÊNCIA EXTERNA ---
                # Se não houver external_reference, ele cai de volta para o payer.email por segurança
                email_real = pagamento_info.get("external_reference") or pagamento_info["payer"]["email"]
                email_pagador = email_real.lower().strip()
                
                existe = supabase.table("usuarios_pagos").select("*").eq("email", email_pagador).execute()
                
                if existe.data and len(existe.data) > 0:
                    usuario_atual = existe.data[0]
                    creditos_atuais = usuario_atual["creditos"] + 50
                    supabase.table("usuarios_pagos").update({"creditos": creditos_atuais}).eq("email", email_pagador).execute()
                else:
                    supabase.table("usuarios_pagos").insert({"email": email_pagador, "creditos": 50}).execute()
                    
        except Exception as e:
            print(f"Erro interno no processamento do webhook: {e}")
            return Response(status_code=status.HTTP_200_OK)
                
    return Response(status_code=status.HTTP_200_OK)


# ROTA DE CONSULTA REFORÇADA COM TEXTO PURO (IMPOSSÍVEL DO NAVEGADOR BLOQUEAR)
@app.get("/checar-creditos", response_class=PlainTextResponse)
async def checar_creditos(email: str):
    user_query = supabase.table("usuarios_pagos").select("creditos").eq("email", email.strip().lower()).execute()
    if user_query.data and len(user_query.data) > 0:
        # Retorna apenas o número puro em formato de texto (Ex: "50")
        return str(user_query.data[0]["creditos"])
    return "0"


@app.get("/contato", response_class=HTMLResponse)
async def pagina_contato(request: Request):
    return templates.TemplateResponse("contato.html", {"request": request, "sucesso": False})

@app.get("/", response_class=HTMLResponse)
async def pagina_principal():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Meu App</title>
        <!-- O CSS fica direto aqui dentro, sem precisar de arquivos -->
        <style>
            body {
                font-family: Arial, sans-serif;
                background-color: #f4f4f9;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Formulário de Contato</h1>
            <!-- Seu HTML aqui -->
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

    # --- Se passar na validação, continua o fluxo normal ---
    
    # 1. Salva a mensagem no Supabase
    supabase.table("contatos").insert({
        "nome": nome,
        "email": email,
        "mensagem": mensagem
    }).execute()
    
    # 2. Envia a notificação por e-mail via Resend
    try:
        params = {
            "from": "onboarding@resend.dev",
            "to": ["lucychatiaonlinel@gmail.com"],  # Modifique para o seu e-mail de destino
            "subject": f"Novo contato do site: {nome}",
            "html": f"<h3>Novo contato</h3><p><b>Nome:</b> {nome}</p><p><b>E-mail:</b> {email}</p><p><b>Mensagem:</b> {mensagem}</p>"
        }
        resend.Emails.send(params)
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
    
    return templates.TemplateResponse("contato.html", {"request": request, "sucesso": True})

