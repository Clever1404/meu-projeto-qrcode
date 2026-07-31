import os
import io
import base64
import qrcode
import crcmod
import unicodedata
import mercadopago
import smtplib
from typing import Annotated
from fastapi import FastAPI, Request, Form, status, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from supabase import create_client, Client
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from itsdangerous import Signer, BadSignature
from dotenv import load_dotenv
import bcrypt

app = FastAPI()
load_dotenv()

# Configuração de caminhos absolutos para os templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Configurações do Mercado Pago, Supabase e E-mail via Variáveis de Ambiente
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN") or "SEU_TOKEN_AQUI"
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "SUA_URL_AQUI"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or "SUA_CHAVE_AQUI"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")

# Configuração de Criptografia e Sessão por Cookies
COOKIE_SECRET = os.environ.get("COOKIE_SECRET", "uma-chave-muito-segura-e-secreta")
signer = Signer(COOKIE_SECRET)

# Removemos o pwd_context e usamos funções limpas para criptografia
def criptografar_senha(senha: str) -> str:
    # Converte a string da senha para bytes, gera o salt e faz o hash
    senha_bytes = senha.encode('utf-8')
    salt = bcrypt.gensalt()
    senha_hash = bcrypt.hashpw(senha_bytes, salt)
    return senha_hash.decode('utf-8')

def verificar_senha(senha_digitada: str, senha_banco: str) -> bool:
    try:
        return bcrypt.checkpw(senha_digitada.encode('utf-8'), senha_banco.encode('utf-8'))
    except Exception:
        return False


def obter_usuario_logado(request: Request) -> str | None:
    cookie_usuario = request.cookies.get("usuario_email")
    if not cookie_usuario:
        return None
    try:
        return signer.unsign(cookie_usuario.encode()).decode()
    except BadSignature:
        return None


@app.get("/contato", response_class=HTMLResponse)
async def pagina_contato(request: Request):
    # Passa o request diretamente como o primeiro argumento nomeado exigido pela sua versão do FastAPI
    return templates.TemplateResponse(
        request=request, 
        name="contato.html", 
        context={"sucesso": False}
    )

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
    
    # 2. Envia a notificação por e-mail via SMTP:
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Formulário do Site <{EMAIL_USER}>"
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = f"Novo contato do site: {nome}"

        corpo_html = f"<h3>Novo contato</h3><p><b>Nome:</b> {nome}</p><p><b>E-mail:</b> {email}</p><p><b>Mensagem:</b> {mensagem}</p>"
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        with smtplib.SMTP("://gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_RECEIVER, msg.as_string())
            
        print("E-mail enviado com sucesso via SMTP!")
    except Exception as e:
        print(f"Erro ao enviar e-mail por SMTP: {e}")

    # Passa o request de forma explícita também na resposta de sucesso
    return templates.TemplateResponse(
        request=request, 
        name="contato.html", 
        context={"sucesso": True}
    )

# --- AUXILIARES E LAYOUTS PIX HOMOLOGADOS ---

def limpar_texto(texto):
    return "".join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    ).upper().replace('$', '').replace('@', '@')

def gerar_payload_pix_estrito(chave, nome, cidade, valor, txid="***"):
    nome = limpar_texto(nome)[:25]
    cidade = limpar_texto(cidade)[:15]
    txid = limpar_texto(txid)[:25]
    payload_format_indicator = "000201"
    gui = "0014BR.GOV.BCB.PIX"
    sub_bloco_chave = f"01{len(chave):02d}{chave}"
    merchant_account = gui + sub_bloco_chave
    merchant_account_len = f"26{len(merchant_account):02d}{merchant_account}"
    merchant_category_code = "52040000"
    transaction_currency = "5303986"
    transaction_amount = ""
    if valor > 0:
        valor_str = f"{valor:.2f}"
        transaction_amount = f"54{len(valor_str):02d}{valor_str}"
    country_code = "5802BR"
    merchant_name = f"59{len(nome):02d}{nome}"
    merchant_city = f"60{len(cidade):02d}{cidade}"
    additional_data = f"05{len(txid):02d}{txid}"
    additional_data_template = f"62{len(additional_data):02d}{additional_data}"
    
    payload = (
        payload_format_indicator + merchant_account_len + merchant_category_code +
        transaction_currency + transaction_amount + country_code + merchant_name +
        merchant_city + additional_data_template + "6304"
    )
    crc16 = crcmod.mkCrcFun(poly=0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    crc_code = hex(crc16(payload.encode('utf-8')))[2:].upper().zfill(4)
    return payload + crc_code

def gerar_base64_qrcode(payload_pix: str) -> str:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(payload_pix)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"


# =====================================================================
# --- FLUXO DE ROTAS COMERCIAL BLINDADO (SEM DEPENDER DO JINJA2) ---
# =====================================================================

@app.get("/", response_class=HTMLResponse)
async def rota_home(request: Request):
    """1) Página inicial pública (Landing Page / Apresentação)"""
    if obter_usuario_logado(request):
        return RedirectResponse(url="/painel", status_code=status.HTTP_303_SEE_OTHER)
    
    # Gera uma string base64 local para o QR Code de demonstração
    qrcode_exemplo_base64 = gerar_base64_qrcode("QRPixPRO-SistemaHomologado")
    
    caminho_home = os.path.join(BASE_DIR, "templates", "home.html")
    with open(caminho_home, "r", encoding="utf-8") as f:
        html = f.read()
        
    # Injeta a imagem gerada localmente direto na tag correspondente
    html = html.replace('src="https://qrserver.com"', f'src="{qrcode_exemplo_base64}"')
    
    return HTMLResponse(content=html)

# =====================================================================
# --- ROTA GET /PAINEL (ÁREA RESTRITA - CARREGAMENTO INICIAL) ---
# =====================================================================
@app.get("/painel", response_class=HTMLResponse)
async def pagina_inicial_painel(request: Request):
    email_logado = obter_usuario_logado(request)
    if not email_logado:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    creditos = 0
    try:
        user_query = supabase.table("usuarios_pagos").select("creditos").eq("email", email_logado).execute()
        dados_lista = user_query.data if user_query.data else []
        # CORREÇÃO: Verifica se a lista tem registros e acessa o primeiro item com índice [0]
        if len(dados_lista) > 0:
            primeiro_registro = dados_lista[0]
            creditos = int(primeiro_registro.get("creditos", 0))
    except Exception as e:
        print(f"Erro ao buscar creditos no GET: {e}")

    erro_url = dict(request.query_params).get("erro_pagamento", "")
    bloco_erro = f'<div style="color:red; font-size:13px; margin-bottom:15px; font-weight:bold;">⚠️ {erro_url}</div>' if erro_url else ""

    caminho_index = os.path.join(BASE_DIR, "templates", "index.html")
    with open(caminho_index, "r", encoding="utf-8") as f:
        html = f.read()

    bloco_dinamico = """
    <div style="border: 2px dashed #e5e7eb; text-align:center; padding: 40px; border-radius:16px; color:#9ca3af;">
        <p style="margin:0; font-size:14px; font-weight:bold;">ATENÇÃO PARA O PREENCHIMENTO</p>
        <p style="margin:5px 0 0 0; font-size:11px;">Preencha os campos obrigatórios em (*) para gerar o QRCode ou realize uma recarga.</p>
    </div>
    """

    # Injeções de texto e preenchimento correto do placeholder do e-mail
    html = html.replace("{{ usuario_logado }}", str(email_logado))
    html = html.replace("{{ creditos_atuais }}", str(creditos))
    html = html.replace("VALUE_EMAIL_PLACEHOLDER", str(email_logado))
    html = html.replace("<!-- ERRO_PAINEL_PLACEHOLDER -->", bloco_erro)
    html = html.replace("<!-- CONTEUDO_DINAMICO_PAINEL -->", bloco_dinamico)
    
    return HTMLResponse(content=html)


# =====================================================================
# --- ROTAS DE AUTENTICAÇÃO BLINDADAS (100% INDEPENDENTES DE JINJA2) ---
# =====================================================================

@app.get("/login", response_class=HTMLResponse)
async def pagina_login(request: Request):
    if obter_usuario_logado(request):
        return RedirectResponse(url="/painel", status_code=status.HTTP_303_SEE_OTHER)
    
    caminho_login = os.path.join(BASE_DIR, "templates", "login.html")
    with open(caminho_login, "r", encoding="utf-8") as f:
        html = f.read().replace("<!-- ALERTA_PLACEHOLDER -->", "")
        return HTMLResponse(content=html)


@app.post("/login")
async def processar_login(request: Request, email: str = Form(...), senha: str = Form(...)):
    email_verificar = email.strip().lower()
    resposta = supabase.table("usuarios_pagos").select("*").eq("email", email_verificar).execute()

    caminho_login = os.path.join(BASE_DIR, "templates", "login.html")
    with open(caminho_login, "r", encoding="utf-8") as f:
        html_base = f.read()

    if not resposta.data or len(resposta.data) == 0:
        bloco_erro = '<div class="alert-container" style="background-color: #f8d7da; color: #721c24; padding: 10px;">E-mail ou senha incorretos.</div>'
        return HTMLResponse(content=html_base.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_erro))

    usuario = resposta.data[0]
    if "senha_hash" not in usuario or not usuario["senha_hash"] or not verificar_senha(senha, usuario["senha_hash"]):
        bloco_erro = '<div class="alert-container" style="background-color: #f8d7da; color: #721c24; padding: 10px;">E-mail ou senha incorretos.</div>'
        return HTMLResponse(content=html_base.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_erro))

    response = RedirectResponse(url="/painel", status_code=status.HTTP_303_SEE_OTHER)
    cookie_valor = signer.sign(email_verificar.encode()).decode()
    response.set_cookie(key="usuario_email", value=cookie_valor, httponly=True, max_age=86400)
    return response


@app.get("/cadastro", response_class=HTMLResponse)
async def pagina_cadastro(request: Request):
    if obter_usuario_logado(request):
        return RedirectResponse(url="/painel", status_code=status.HTTP_303_SEE_OTHER)
    
    caminho_cadastro = os.path.join(BASE_DIR, "templates", "cadastro.html")
    with open(caminho_cadastro, "r", encoding="utf-8") as f:
        html = f.read().replace("<!-- ALERTA_PLACEHOLDER -->", "")
        return HTMLResponse(content=html)


@app.post("/cadastro")
async def processar_cadastro(request: Request, nome: str = Form(...), email: str = Form(...), senha: str = Form(...)):
    email_cadastro = email.strip().lower()
    usuario_existente = supabase.table("usuarios_pagos").select("*").eq("email", email_cadastro).execute()
    senha_criptografada = criptografar_senha(senha)

    caminho_cadastro = os.path.join(BASE_DIR, "templates", "cadastro.html")
    with open(caminho_cadastro, "r", encoding="utf-8") as f:
        html_cadastro_base = f.read()

    if usuario_existente.data and len(usuario_existente.data) > 0:
        usuario_atual = usuario_existente.data[0]
        if not usuario_atual.get("senha_hash"):
            supabase.table("usuarios_pagos").update({
                "nome": nome, 
                "senha_hash": senha_criptografada
            }).eq("email", email_cadastro).execute()
        else:
            bloco_erro = '<div class="alert-container" style="background-color: #f8d7da; color: #721c24; padding: 10px;">Este e-mail já está cadastrado.</div>'
            return HTMLResponse(content=html_cadastro_base.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_erro))
    else:
        supabase.table("usuarios_pagos").insert({
            "nome": nome, 
            "email": email_cadastro, 
            "senha_hash": senha_criptografada, 
            "creditos": 3
        }).execute()

    # Sucesso: Carrega o arquivo do login e injeta o alerta de sucesso verde nele
    caminho_login = os.path.join(BASE_DIR, "templates", "login.html")
    with open(caminho_login, "r", encoding="utf-8") as f:
        html_login = f.read()
        bloco_sucesso = '<div class="alert-container" style="background-color: #d4edda; color: #155724; padding: 10px;">Conta criada com sucesso! Faça seu login.</div>'
        return HTMLResponse(content=html_login.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_sucesso))


@app.get("/logout")
async def processar_logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="usuario_email")
    return response



# =====================================================================
# --- ROTA POST /PAINEL (QUANDO O CLIENTE GERA UM QR CODE PIX) ---
# =====================================================================
@app.post("/painel", response_class=HTMLResponse)
async def criar_qrcode(
    request: Request,
    chave: Annotated[str | None, Form()] = None,
    nome: Annotated[str | None, Form()] = None,
    cidade: Annotated[str | None, Form()] = None,
    valor: Annotated[float | None, Form()] = None,
    email_cliente: Annotated[str | None, Form()] = None
):
    email_logado = obter_usuario_logado(request)
    if not email_logado:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    email_final = email_cliente.strip().lower() if email_cliente else email_logado.strip().lower()

    if not all([chave, nome, cidade]) or valor is None:
        return RedirectResponse(url="/painel?erro_pagamento=Preencha+todos+os+campos", status_code=status.HTTP_303_SEE_OTHER)

    creditos_atuais = 0
    try:
        user_query = supabase.table("usuarios_pagos").select("*").eq("email", email_final).execute()
        dados_lista = user_query.data if user_query.data else []
        
        # CORREÇÃO: Acessa o índice [0] da lista do Supabase com segurança
        if not dados_lista or len(dados_lista) == 0:
            supabase.table("usuarios_pagos").insert({"email": email_final, "creditos": 2}).execute()
            creditos_atuais = 3
        else:
            primeiro_registro = dados_lista[0]
            creditos_atuais = int(primeiro_registro.get("creditos", 0))
    except Exception as e:
        print(f"Erro ao consultar saldo no POST: {e}")

    if creditos_atuais <= 0:
        return RedirectResponse(url="/painel?erro_pagamento=Seus+creditos+acabaram.+Realize+uma+recarga.", status_code=status.HTTP_303_SEE_OTHER)

    novos_creditos = creditos_atuais - 1
    supabase.table("usuarios_pagos").update({"creditos": novos_creditos}).eq("email", email_final).execute()

    payload_pix = gerar_payload_pix_estrito(chave, nome, cidade, valor)
    qrcode_base64 = gerar_base64_qrcode(payload_pix)
    
    # CORREÇÃO: Removido o operador de atribuição inválida 'city := cidade' de dentro do dicionário
    supabase.table("qrcodes").insert({
        "chave": chave, 
        "nome": nome, 
        "cidade": cidade, 
        "valor": valor, 
        "payload_pix": payload_pix, 
        "image_url": qrcode_base64
    }).execute()
    
    caminho_index = os.path.join(BASE_DIR, "templates", "index.html")
    with open(caminho_index, "r", encoding="utf-8") as f:
        html = f.read()

    bloco_qrcode = f"""
    <div class="card-panel" style="text-align: center; background: white; padding: 25px; border-radius: 16px; border: 1px solid #e5e7eb;">
        <span style="color:#16a34a; font-weight:bold; font-size:12px;">✓ QR Code Ativo</span>
        <img src="{qrcode_base64}" style="width:160px; height:160px; display:block; margin: 15px auto; border:1px solid #e5e7eb; padding:5px; border-radius:5px;">
        <textarea id="copia_cola" readonly style="width:100%; height:45px; font-size:11px; text-align:center; border:1px solid #e5e7eb; border-radius:5px; resize:none; padding:5px; box-sizing:border-box; margin-bottom:10px;">{payload_pix}</textarea>
        <button onclick="navigator.clipboard.writeText(document.getElementById('copia_cola').value); mostrarPopup('Código Copiado!')" style="width:100%; padding:10px; background:#111827; color:white; border:none; font-weight:bold; border-radius:5px; cursor:pointer; margin-bottom:5px;">Copiar Pix</button>
        <a href="{qrcode_base64}" download="pix.png" style="display:block; text-decoration:none; padding:10px; background:#f3f4f6; color:#111827; font-weight:bold; border-radius:8px; font-size:13px; text-align:center;">Baixar Imagem</a>
    </div>
    """

    html = html.replace("{{ usuario_logado }}", str(email_logado))
    html = html.replace("{{ creditos_atuais }}", str(novos_creditos))
    html = html.replace("VALUE_EMAIL_PLACEHOLDER", str(email_final))
    html = html.replace("<!-- ERRO_PAINEL_PLACEHOLDER -->", "")
    html = html.replace("<!-- CONTEUDO_DINAMICO_PAINEL -->", bloco_qrcode)
    
    return HTMLResponse(content=html)

# --- FLUXO DE COMPRA DE CRÉDITOS ---

# =====================================================================
# --- ROTA POST /COMPRAR-CREDITOS (SOLICITAÇÃO DE RECARGA MERCADO PAGO) ---
# =====================================================================
@app.post("/comprar-creditos", response_class=HTMLResponse)
async def comprar_creditos(request: Request):
    email_logado = obter_usuario_logado(request)
    if not email_logado:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        
    payment_data = {
        "transaction_amount": 19.90,
        "description": "Recarga 50 Créditos - QR Pix Pro",
        "payment_method_id": "pix",
        "external_reference": email_logado,
        "payer": {"email": email_logado}
    }
    
    caminho_index = os.path.join(BASE_DIR, "templates", "index.html")
    with open(caminho_index, "r", encoding="utf-8") as f:
        html = f.read()

    creditos = 0
    try:
        user_query = supabase.table("usuarios_pagos").select("creditos").eq("email", email_logado).execute()
        if user_query.data and len(user_query.data) > 0:
            creditos = int(user_query.data[0].get("creditos", 0))
            
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response["response"]
        pix_copia_cola = payment["point_of_interaction"]["transaction_data"]["qr_code"]
        pix_qr_base64 = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        checkout_qr_url = f"data:image/png;base64,{pix_qr_base64}"

        # O Python monta o painel de Checkout com o script de checagem em tempo real embutido
        bloco_checkout = f"""
        <div class="card-panel" id="painel-mercado-pago" style="background: #1e1b4b; color: white; text-align: center; padding: 25px; border-radius:16px;">
            <h4>Recarga de Saldo Gerada</h4>
            <img src="{checkout_qr_url}" style="width:140px; height:140px; margin-bottom:15px; background: white; padding:5px; border-radius:5px;">
            <input type="text" id="mp_token" value="{pix_copia_cola}" readonly style="width:100%; text-align:center; padding:8px; background:rgba(255,255,255,0.1); color:white; border:none; border-radius:5px; margin-bottom:10px; font-size:11px;">
            <button onclick="navigator.clipboard.writeText(document.getElementById('mp_token').value); mostrarPopup('Pix copiado!')" style="width:100%; padding:10px; background:#22c55e; color:white; border:none; font-weight:bold; border-radius:5px; cursor:pointer;">Copiar Código Pix</button>
        </div>

        <script>
            var emailSolicitado = "{email_logado}";
            var saldoInicial = parseInt("{creditos}", 10);
            window.intervaloChecagem = setInterval(async function() {{
                try {{
                    var resposta = await fetch("/checar-creditos?email=" + encodeURIComponent(emailSolicitado));
                    var textoSaldo = await resposta.text();
                    var saldoAtual = parseInt(textoSaldo, 10);
                    if (!isNaN(saldoAtual) && saldoAtual > saldoInicial) {{
                        clearInterval(window.intervaloChecagem);
                        mostrarPopup("Créditos adicionados com sucesso!");
                        setTimeout(function() {{ window.location.href = "/painel"; }}, 2000);
                    }}
                }} catch (e) {{}}
            }}, 4000);
        </script>
        """
    # ... (Seu código anterior do Mercado Pago dentro da rota continua igual) ...
    except Exception as e:
        print(f"Erro no MP: {e}")
        return RedirectResponse(url="/painel?erro_pagamento=Erro+Mercado+Pago", status_code=status.HTTP_303_SEE_OTHER)

    # # =====================================================================
    # # BLOCO DE CORREÇÃO: INJEÇÃO COMPLETA DE VARIÁVEIS NA TELA DE RECARGA
    # # =====================================================================
    html = html.replace("{{ usuario_logado }}", str(email_logado))
    html = html.replace("{{ creditos_atuais }}", str(creditos))
    
    # CORREÇÃO CRÍTICA: Preenche o e-mail real do usuário no campo ao carregar a tela de checkout
    html = html.replace("VALUE_EMAIL_PLACEHOLDER", str(email_logado))
    
    html = html.replace("<!-- ERRO_PAINEL_PLACEHOLDER -->", "")
    html = html.replace("<!-- CONTEUDO_DINAMICO_PAINEL -->", bloco_checkout)
    
    return HTMLResponse(content=html)


@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request, response: Response, id: str | None = None, topic: str | None = None):
    id_pagamento = id or dict(request.query_params).get("data.id")
    if id_pagamento and str(id_pagamento) != "123456":
        try:
            # 1. VERIFICAÇÃO ANTIDUPLICIDADE: Verifica se esse ID de Pix já foi processado antes
            ja_processado = supabase.table("pagamentos_processados").select("*").eq("id_pagamento", str(id_pagamento)).execute()
            if ja_processado.data:
                print(f"Webhook ignorado: Pagamento {id_pagamento} já tinha sido computado.")
                return Response(status_code=status.HTTP_200_OK) # Retorna 200 pro MP parar de enviar

            pagamento_response = sdk.payment().get(id_pagamento)
            pagamento_info = pagamento_response.get("response", {})
            
            if pagamento_info.get("status") == "approved":
                email_real = pagamento_info.get("external_reference") or pagamento_info["payer"]["email"]
                email_pagador = email_real.lower().strip()
                
                # 2. Registra o ID imediatamente para bloquear outras tentativas simultâneas
                supabase.table("pagamentos_processados").insert({"id_pagamento": str(id_pagamento), "email": email_pagador}).execute()
                
                # 3. Processa o saldo normalmente
                existe = supabase.table("usuarios_pagos").select("*").eq("email", email_pagador).execute()
                if existe.data:
                    creditos_atuais = existe.data[0]["creditos"] + 50  # Aqui vai adicionar apenas 2 uma única vez
                    supabase.table("usuarios_pagos").update({"creditos": creditos_atuais}).eq("email", email_pagador).execute()
                else:
                    supabase.table("usuarios_pagos").insert({"email": email_pagador, "creditos": 50}).execute()
                    
        except Exception as e:
            print(f"Erro webhook: {e}")
            
    return Response(status_code=status.HTTP_200_OK)


@app.get("/checar-creditos", response_class=PlainTextResponse)
async def checar_creditos(email: str):
    user_query = supabase.table("usuarios_pagos").select("creditos").eq("email", email.strip().lower()).execute()
    if user_query.data and len(user_query.data) > 0:
        return str(user_query.data[0]["creditos"])
    return "0"

