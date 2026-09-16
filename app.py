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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from itsdangerous import Signer, BadSignature
from dotenv import load_dotenv
import bcrypt
import re

# --- NOVAS IMPORTAÇÕES PARA O RENDER/POSTGRESQL ---
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
from sqlalchemy import Float

app = FastAPI()
load_dotenv()

# Configuração de caminhos absolutos para os templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Configurações do Mercado Pago, Render e E-mail via Variáveis de Ambiente
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN") or "SEU_TOKEN_AQUI"
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

# Substituído Supabase por PostgreSQL do Render
DATABASE_URL = os.environ.get("DATABASE_URL") or "postgresql+psycopg://qrpix_prod_db_user:qfdxRojE0VPSf2KdaFavak5ZTInRfDKD@dpg-dadvtf2d0e5s73ej3jb0-a/qrpix_prod_db"

# Configuração do SQLAlchemy
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Definição do modelo/tabela para o Render
class Contato(Base):
    __tablename__ = "contatos"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    mensagem = Column(Text, nullable=False)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)

# Cria a tabela automaticamente no Render caso ela não exista
Base.metadata.create_all(bind=engine)

# Atualize o seu modelo UsuarioPago para incluir a coluna 'nome' que faltava para o cadastro
class UsuarioPago(Base):
    __tablename__ = "usuarios_pagos"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=True) # Adicionado para suportar o cadastro
    email = Column(String(255), unique=True, index=True, nullable=False)
    creditos = Column(Integer, default=2)
    senha_hash = Column(String(255), nullable=True)

# Cria todas as tabelas (incluindo usuários) automaticamente ao iniciar
Base.metadata.create_all(bind=engine)


# Nova tabela para salvar os históricos de pix gerados no Render
class QRCodeModel(Base):
    __tablename__ = "qrcodes"
    
    id = Column(Integer, primary_key=True, index=True)
    chave = Column(String(255), nullable=False)
    nome = Column(String(255), nullable=False)
    cidade = Column(String(255), nullable=False)
    valor = Column(Float, nullable=False)
    payload_pix = Column(Text, nullable=False)
    image_url = Column(Text, nullable=False)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)

# Garante que as novas tabelas/colunas sejam criadas no Render
Base.metadata.create_all(bind=engine)

# Nova tabela para travar pagamentos duplicados no Render
class PagamentoProcessado(Base):
    __tablename__ = "pagamentos_processados"
    
    id = Column(Integer, primary_key=True, index=True)
    id_pagamento = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(255), nullable=False)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)

# Garante a criação da nova tabela no Render
Base.metadata.create_all(bind=engine)


EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")

# Configuração de Criptografia e Sessão por Cookies
COOKIE_SECRET = os.environ.get("COOKIE_SECRET", "uma-chave-muito-segura-e-secreta")
signer = Signer(COOKIE_SECRET)

# Funções limpas para criptografia
def criptografar_senha(senha: str) -> str:
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
    # 1. Salva a mensagem no Render via SQLAlchemy
    db = SessionLocal()
    try:
        novo_contato = Contato(nome=nome, email=email, messaging=mensagem) # se preferir usar coluna 'mensagem' altere o parâmetro para mensagem=mensagem
        novo_contato.mensagem = mensagem
        db.add(novo_contato)
        db.commit()
    except Exception as db_err:
        db.rollback()
        print(f"Erro ao salvar no banco do Render: {db_err}")
    finally:
        db.close()
    
    # 2. Envia a notificação por e-mail via SMTP:
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Formulário do Site <{EMAIL_USER}>"
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = f"Novo contato do site: {nome}"

        corpo_html = f"<h3>Novo contato</h3><p><b>Nome:</b> {nome}</p><p><b>E-mail:</b> {email}</p><p><b>Mensagem:</b> {mensagem}</p>"
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server: # Corrigido de "://gmail.com" para "smtp.gmail.com"
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_RECEIVER, msg.as_string())
            
        print("E-mail enviado com sucesso via SMTP!")
    except Exception as e:
        print(f"Erro ao enviar e-mail por SMTP: {e}")

    return templates.TemplateResponse(
        request=request, 
        name="contato.html", 
        context={"sucesso": True}
    )

# --- AUXILIARES E LAYOUTS PIX HOMOLOGADOS ---
def limpar_texto(texto):
    if not texto:
        return ""
    # Remove acentos
    texto = "".join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    ).upper()
    # Mantém APENAS letras, números e espaços (regras estritas do BB)
    return re.sub(r'[^A-Z0-9 ]', '', texto).strip()

def tratar_chave_pix(chave):
    chave = str(chave).strip()
    
    # 1. Se for e-mail ou chave aleatória (UUID), mantém original em minúsculas
    if "@" in chave or ("-" in chave and len(chave) == 36):
        return chave.lower()
        
    # Remove qualquer caractere que não seja número
    apenas_numeros = re.sub(r'[^0-9]', '', chave)
    
    # 2. Se a chave explicitamente começa com "+" é um telefone
    if chave.startswith("+"):
        if not apenas_numeros.startswith("55"):
            apenas_numeros = f"55{apenas_numeros}"
        return f"+{apenas_numeros}"
    
    # 3. Se tem 11 dígitos, pode ser CPF ou Telefone (ex: 11999999999)
    if len(apenas_numeros) == 11:
        # Regra de ouro: Celulares no Brasil têm o formato DDD + 9 + 8 dígitos.
        # Portanto, o terceiro dígito de um número de celular de 11 posições é SEMPRE 9.
        # Se o terceiro dígito NÃO for 9, com certeza é um CPF.
        if apenas_numeros[2] == '9':
            if not apenas_numeros.startswith("55"):
                apenas_numeros = f"55{apenas_numeros}"
            return f"+{apenas_numeros}"
        else:
            return apenas_numeros
            
    # 4. Se tem 10 dígitos (Fixo: DDD + 8 dígitos) ou 12/13 dígitos (já com o 55)
    if len(apenas_numeros) in:
        if not apenas_numeros.startswith("55"):
            apenas_numeros = f"55{apenas_numeros}"
        return f"+{apenas_numeros}"
        
    # 5. Para CNPJ (14 dígitos) ou qualquer outro caso numérico limpo
    return apenas_numeros

    
def gerar_payload_pix_estrito(chave_bruta, nome, cidade, valor, txid="***"):
    # 1. Trata e formata a chave antes de qualquer cálculo
    chave = tratar_chave_pix(chave_bruta)
    
    nome = limpar_texto(nome)[:25]
    cidade = limpar_texto(cidade)[:15]
    
    # 2. Força o TXID padrão aceito pelo BB se for nulo/vazio ou asteriscos
    if not txid or txid.strip() == "" or "***" in txid:
        txid_limpo = "***"
    else:
        txid_limpo = re.sub(r'[^A-Z0-9]', '', txid.upper())[:25]
        if not txid_limpo:
            txid_limpo = "***"

    # 3. Montagem dos blocos com cálculo dinâmico de tamanho (Crucial para o BB)
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
    
    additional_data = f"05{len(txid_limpo):02d}{txid_limpo}"
    additional_data_template = f"62{len(additional_data):02d}{additional_data}"
    
    # Concatenação completa
    payload = (
        payload_format_indicator + merchant_account_len + merchant_category_code +
        transaction_currency + transaction_amount + country_code + merchant_name +
        merchant_city + additional_data_template + "6304"
    )
    
    # Cálculo do CRC16
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
    db = SessionLocal()
    try:
        # Busca o usuário no PostgreSQL do Render pelo e-mail
        usuario_db = db.query(UsuarioPago).filter(UsuarioPago.email == email_logado).first()
        
        # SE NÃO EXISTIR REGISTRO: Cria o usuário direto com 2 créditos
        if not usuario_db:
            usuario_db = UsuarioPago(email=email_logado, creditos=2)
            db.add(usuario_db)
            db.commit()
            db.refresh(usuario_db)
            creditos = 2
        else:
            creditos = int(usuario_db.creditos)
    except Exception as e:
        db.rollback()
        print(f"Erro ao buscar creditos no GET: {e}")
    finally:
        db.close()

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
    
    db = SessionLocal()
    usuario_db = None
    try:
        # Busca o usuário correspondente no Render
        usuario_db = db.query(UsuarioPago).filter(UsuarioPago.email == email_verificar).first()
    except Exception as e:
        print(f"Erro ao autenticar no banco: {e}")
    finally:
        db.close()

    caminho_login = os.path.join(BASE_DIR, "templates", "login.html")
    with open(caminho_login, "r", encoding="utf-8") as f:
        html_base = f.read()

    # Se o usuário não existir no banco
    if not usuario_db:
        bloco_erro = '<div class="alert-container" style="background-color: #f8d7da; color: #721c24; padding: 10px;">E-mail ou senha incorretos.</div>'
        return HTMLResponse(content=html_base.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_erro))

    # Validação do campo de senha_hash usando o objeto retornado do SQLAlchemy
    if not usuario_db.senha_hash or not verificar_senha(senha, usuario_db.senha_hash):
        bloco_erro = '<div class="alert-container" style="background-color: #f8d7da; color: #721c24; padding: 10px;">E-mail ou senha incorretos.</div>'
        return HTMLResponse(content=html_base.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_erro))

    response = RedirectResponse(url="/painel", status_code=status.HTTP_303_SEE_OTHER)
    cookie_valor = signer.sign(email_verificar.encode()).decode()
    response.set_cookie(key="usuario_email", value=cookie_valor, httponly=True, max_age=86400)
    return response


# =====================================================================
# --- CONTINUAÇÃO DAS ROTAS DE AUTENTICAÇÃO ---
# =====================================================================

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
    senha_criptografada = criptografar_senha(senha)

    caminho_cadastro = os.path.join(BASE_DIR, "templates", "cadastro.html")
    with open(caminho_cadastro, "r", encoding="utf-8") as f:
        html_cadastro_base = f.read()

    db = SessionLocal()
    try:
        # Busca usuário existente no Render
        usuario_atual = db.query(UsuarioPago).filter(UsuarioPago.email == email_cadastro).first()

        if usuario_atual:
            # Caso o usuário exista (ex: veio via webhook sem senha), atualiza com a senha criada
            if not usuario_atual.senha_hash:
                usuario_atual.nome = nome
                usuario_atual.senha_hash = senha_criptografada
                db.commit()
            else:
                bloco_erro = '<div class="alert-container" style="background-color: #f8d7da; color: #721c24; padding: 10px;">Este e-mail já está cadastrado.</div>'
                return HTMLResponse(content=html_cadastro_base.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_erro))
        else:
            # Cria um novo usuário do zero no Render
            novo_usuario = UsuarioPago(
                nome=nome,
                email=email_cadastro,
                senha_hash=senha_criptografada,
                creditos=2
            )
            db.add(novo_usuario)
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Erro ao processar cadastro no Render: {e}")
        bloco_erro = '<div class="alert-container" style="background-color: #f8d7da; color: #721c24; padding: 10px;">Erro interno ao salvar cadastro.</div>'
        return HTMLResponse(content=html_cadastro_base.replace("<!-- ALERTA_PLACEHOLDER -->", bloco_erro))
    finally:
        db.close()

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

    if not all([chave, nome, city := cidade]) or valor is None: # Mantida a correção de segurança preventiva do FastAPI
        return RedirectResponse(url="/painel?erro_pagamento=Preencha+todos+os+campos", status_code=status.HTTP_303_SEE_OTHER)

    creditos_atuais = 0
    db = SessionLocal()
    
    try:
        # Busca ou cria o usuário para verificar créditos no Render
        usuario_db = db.query(UsuarioPago).filter(UsuarioPago.email == email_final).first()
        
        if not usuario_db:
            usuario_db = UsuarioPago(email=email_final, creditos=2)
            db.add(usuario_db)
            db.commit()
            db.refresh(usuario_db)
            creditos_atuais = 2
        else:
            creditos_atuais = int(usuario_db.creditos)
            
        if creditos_atuais <= 0:
            return RedirectResponse(url="/painel?erro_pagamento=Seus+creditos+acabaram.+Realize+uma+recarga.", status_code=status.HTTP_303_SEE_OTHER)

        # Desconta um crédito do usuário
        novos_creditos = creditos_atuais - 1
        usuario_db.creditos = novos_creditos
        
        # Gera o Pix
        payload_pix = gerar_payload_pix_estrito(chave, nome, cidade, valor)
        qrcode_base64 = gerar_base64_qrcode(payload_pix)
        
        # Insere o registro do QRCode gerado no Render
        novo_qr = QRCodeModel(
            chave=chave,
            nome=nome,
            cidade=cidade,
            valor=valor,
            payload_pix=payload_pix,
            image_url=qrcode_base64
        )
        db.add(novo_qr)
        
        # Salva as duas operações (update de créditos + insert de qrcode) de forma atômica
        db.commit()
        
    except Exception as e:
        db.rollback()
        print(f"Erro ao processar transação de Pix no Render: {e}")
        return RedirectResponse(url="/painel?erro_pagamento=Erro+interno+ao+gerar+Pix", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        db.close()

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
# --- ROTA POST /COMPRAR-CREDITOS (SOLICITAÇÃO DE RECARGA) ---
# =====================================================================
@app.post("/comprar-creditos", response_class=HTMLResponse)
async def comprar_creditos(request: Request):
    email_logado = obter_usuario_logado(request)
    if not email_logado:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        
    payment_data = {
        "transaction_amount": 5.90,  # 🌟 Valor de R$ 5,90
        "description": "Recarga 5 Créditos - QR Pix Pro",  # Pacote de 5 créditos
        "payment_method_id": "pix",
        "external_reference": email_logado,
        "payer": {"email": email_logado},
        "notification_url": "https://qrpixpro.com.br"
    }
    
    caminho_index = os.path.join(BASE_DIR, "templates", "index.html")
    with open(caminho_index, "r", encoding="utf-8") as f:
        html = f.read()

    creditos = 0
    db = SessionLocal()
    try:
        # Busca créditos atuais no Render
        usuario_db = db.query(UsuarioPago).filter(UsuarioPago.email == email_logado).first()
        if usuario_db:
            creditos = int(usuario_db.creditos)
            
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response["response"]
        
        id_do_pagamento_criado = str(payment.get("id"))
        pix_copia_cola = payment["point_of_interaction"]["transaction_data"]["qr_code"]
        pix_qr_base64 = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        checkout_qr_url = f"data:image/png;base64,{pix_qr_base64}"

        bloco_checkout = f"""
        <div class="card-panel" id="painel-mercado-pago" style="background: #1e1b4b; color: white; text-align: center; padding: 25px; border-radius:16px;">
            <h4>Recarga de Saldo Gerada (5 Créditos)</h4>
            <img src="{checkout_qr_url}" style="width:140px; height:140px; margin-bottom:15px; background: white; padding:5px; border-radius:5px;">
            <input type="text" id="mp_token" value="{pix_copia_cola}" readonly style="width:100%; text-align:center; padding:8px; background:rgba(255,255,255,0.1); color:white; border:none; border-radius:5px; margin-bottom:10px; font-size:11px;">
            <button onclick="navigator.clipboard.writeText(document.getElementById('mp_token').value); mostrarPopup('Pix copiado!')" style="width:100%; padding:10px; background:#22c55e; color:white; border:none; font-weight:bold; border-radius:5px; cursor:pointer;">Copiar Código Pix</button>
        </div>

        <script>
            var emailSolicitado = "{email_logado}";
            var idPagamento = "{id_do_pagamento_criado}"; 
            var saldoInicial = parseInt("{creditos}", 10);
            
            window.intervaloChecagem = setInterval(async function() {{
                try {{
                    var resposta = await fetch("/checar-creditos?email=" + encodeURIComponent(emailSolicitado) + "&id_pagamento=" + idPagamento);
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
    except Exception as e:
        print(f"Erro no MP: {e}")
        return RedirectResponse(url="/painel?erro_pagamento=Erro+Mercado+Pago", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        db.close()

    html = html.replace("{{ usuario_logado }}", str(email_logado))
    html = html.replace("{{ creditos_atuais }}", str(creditos))
    html = html.replace("VALUE_EMAIL_PLACEHOLDER", str(email_logado))
    html = html.replace("<!-- ERRO_PAINEL_PLACEHOLDER -->", "")
    html = html.replace("<!-- CONTEUDO_DINAMICO_PAINEL -->", bloco_checkout)
    
    return HTMLResponse(content=html)


# =====================================================================
# --- ROTA POST /WEBHOOK/MERCADOPAGO (ENTREGA AUTOMÁTICA) ---
# =====================================================================
@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request, response: Response, id: str | None = None, topic: str | None = None):
    id_pagamento = id or dict(request.query_params).get("data.id")
    if id_pagamento and str(id_pagamento) != "123456":
        db = SessionLocal()
        try:
            # 1. Trava antiduplicidade nativa via SQL
            ja_processado = db.query(PagamentoProcessado).filter(PagamentoProcessado.id_pagamento == str(id_pagamento)).first()
            if ja_processado:
                return Response(status_code=status.HTTP_200_OK)

            pagamento_response = sdk.payment().get(id_pagamento)
            pagamento_info = pagamento_response.get("response", {})
            
            if pagamento_info.get("status") == "approved":
                email_real = pagamento_info.get("external_reference") or pagamento_info["payer"]["email"]
                email_pagador = email_real.lower().strip()
                
                # Registra o ID de pagamento para travar futuras requisições iguais
                novo_processado = PagamentoProcessado(id_pagamento=str(id_pagamento), email=email_pagador)
                db.add(novo_processado)
                
                # 2. Adiciona exatamente 5 créditos
                usuario_db = db.query(UsuarioPago).filter(UsuarioPago.email == email_pagador).first()
                if usuario_db:
                    usuario_db.creditos = int(usuario_db.creditos) + 5
                else:
                    usuario_db = UsuarioPago(email=email_pagador, creditos=5)
                    db.add(usuario_db)
                    
                db.commit()
                print(f"Sucesso Webhook: 5 créditos entregues para {email_pagador}")
        except Exception as e:
            db.rollback()
            print(f"Erro webhook: {e}")
        finally:
            db.close()
            
    return Response(status_code=status.HTTP_200_OK)


# =====================================================================
# --- ROTA GET /CHECAR-CREDITOS (POLLING ATIVO DO FRONTEND) ---
# =====================================================================
@app.get("/checar-creditos")
async def checar_creditos(email: str, id_pagamento: str | None = None):
    email_pagador = email.lower().strip()
    db = SessionLocal()
    
    if id_pagamento:
        try:
            pagamento_response = sdk.payment().get(id_pagamento)
            pagamento_info = pagamento_response.get("response", {})
            
            if pagamento_info.get("status") == "approved":
                ja_processado = db.query(PagamentoProcessado).filter(PagamentoProcessado.id_pagamento == str(id_pagamento)).first()
                
                if not ja_processado:
                    # Registra a trava antiduplicidade
                    novo_processado = PagamentoProcessado(id_pagamento=str(id_pagamento), email=email_pagador)
                    db.add(novo_processado)
                    
                    # Atualiza os créditos
                    usuario_db = db.query(UsuarioPago).filter(UsuarioPago.email == email_pagador).first()
                    if usuario_db:
                        usuario_db.creditos = int(usuario_db.creditos) + 5
                    else:
                        usuario_db = UsuarioPago(email=email_pagador, creditos=5)
                        db.add(usuario_db)
                        
                    db.commit()
        except Exception as e:
            db.rollback()
            print(f"Erro checagem ativa: {e}")

    creditos_finais = 0
    try:
        usuario_final = db.query(UsuarioPago).filter(UsuarioPago.email == email_pagador).first()
        if usuario_final:
            creditos_finais = usuario_final.creditos
    except Exception as e:
        print(f"Erro ao ler saldo final: {e}")
    finally:
        db.close()
        
    return PlainTextResponse(str(creditos_finais))


# =====================================================================
# --- NOVA ROTA DE EMERGÊNCIA (Intercepta o Mercado Pago diretamente na raiz) ---
# =====================================================================
@app.post("/")
async def receber_pagamento_raiz(request: Request):
    # Coleta o ID enviado pelo Mercado Pago (ex: ?data.id=170502590943)
    params = dict(request.query_params)
    id_pagamento = params.get("data.id") or params.get("id")
    
    if id_pagamento and str(id_pagamento) != "123456":
        db = SessionLocal()
        try:
            # 1. Trava antiduplicidade nativa via SQL
            ja_processado = db.query(PagamentoProcessado).filter(PagamentoProcessado.id_pagamento == str(id_pagamento)).first()
            if ja_processado:
                print(f"Pagamento raiz {id_pagamento} já processado anteriormente.")
                return Response(status_code=status.HTTP_200_OK)

            # 2. Busca informações do pagamento no Mercado Pago
            pagamento_response = sdk.payment().get(id_pagamento)
            pagamento_info = pagamento_response.get("response", {})
            
            if pagamento_info.get("status") == "approved":
                email_real = pagamento_info.get("external_reference") or pagamento_info["payer"]["email"]
                email_pagador = email_real.lower().strip()
                
                # Registra o ID de pagamento com segurança no Render para travar duplicidades
                novo_processado = PagamentoProcessado(id_pagamento=str(id_pagamento), email=email_pagador)
                db.add(novo_processado)
                
                # 3. Adiciona os 50 créditos no Render de forma atômica
                usuario_db = db.query(UsuarioPago).filter(UsuarioPago.email == email_pagador).first()
                if usuario_db:
                    usuario_db.creditos = int(usuario_db.creditos) + 50
                else:
                    usuario_db = UsuarioPago(email=email_pagador, creditos=50)
                    db.add(usuario_db)
                
                db.commit()
                print(f"Sucesso! 50 créditos adicionados na marra para: {email_pagador}")
                
        except Exception as e:
            db.rollback()
            print(f"Erro no processamento da raiz: {e}")
        finally:
            db.close()
            
    # Retorna 200 OK para o Mercado Pago saber que recebemos o aviso
    return Response(status_code=status.HTTP_200_OK)
