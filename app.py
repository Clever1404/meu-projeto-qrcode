import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import mysql.connector
from mysql.connector import pooling
import xlsxwriter
from io import BytesIO  # Necessário para gerar o download na memória

# --- CONFIGURAÇÃO DO MYSQL ---
DB_HOST = os.environ.get("DB_HOST")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME = os.environ.get("DB_NAME")
DB_PORT = os.environ.get("DB_PORT", "3306")

@st.cache_resource
def init_connection_pool():
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        st.error("Erro: Variáveis de ambiente do MySQL não configuradas no painel da Render.")
        st.stop()
    
    return pooling.MySQLConnectionPool(
        pool_name="pareto_pool",
        pool_size=5,
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=int(DB_PORT)
    )

# --- FUNÇÃO PARA SALVAL NO BANCO ---
def salvar_dados_mysql(df, nome_arquivo):
    try:
        pool = init_connection_pool()
        conexao = pool.get_connection()
        cursor = conexao.cursor()
        
        sql = """
        INSERT INTO registros_pareto (arquivo_origem, categoria, frequencia) 
        VALUES (%s, %s, %s)
        """
        
        dados_para_inserir = []
        for _, row in df.iterrows():
            dados_para_inserir.append((
                nome_arquivo,
                str(row['Categoria']),
                float(row['Frequencia'])
            ))
        
        cursor.executemany(sql, dados_para_inserir)
        conexao.commit()
        st.success(f"Sucesso! {cursor.rowcount} registros salvos no MySQL.")
        
    except mysql.connector.Error as err:
        st.error(f"Erro no banco de dados MySQL: {err}")
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conexao' in locals(): conexao.close()

# --- FUNÇÃO PARA GERAR EXCEL COM GRAFICO NATIVO ---
def gerar_excel_pareto(df):
    output = BytesIO() # Cria um arquivo virtual na memória RAM
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet("Pareto")
    
    # Cabeçalhos
    worksheet.write('A1', 'Categoria')
    worksheet.write('B1', 'Frequencia')
    worksheet.write('C1', '% Acumulada')
    
    # Escreve os dados processados do dataframe
    row = 1
    for _, item in df.iterrows():
        worksheet.write(row, 0, item['Categoria'])
        worksheet.write(row, 1, item['Frequencia'])
        worksheet.write(row, 2, item['Porcentagem_Acumulada'] / 100) # Formato decimal para o Excel converter em %
        row += 1
        
    # Formatação de porcentagem para a coluna C
    formato_porcentagem = workbook.add_format({'num_format': '0.0%'})
    worksheet.set_column('C:C', None, formato_porcentagem)
    
    # Criando o gráfico de Pareto Nativo (Eixo duplo: Coluna + Linha)
    chart_barra = workbook.add_chart({'type': 'column'})
    chart_linha = workbook.add_chart({'type': 'line'})
    
    # Configura a série de barras (Frequência)
    chart_barra.add_series({
        'categories': f'=Pareto!$A$2:$A${row}',
        'values':     f'=Pareto!$B$2:$B${row}',
        'name':       'Frequência',
        'border':     {'color': '#37536D'},
        'fill':       {'color': '#37536D'},
    })
    
    # Configura a série de linha (Porcentagem) no segundo eixo (Y2)
    chart_linha.add_series({
        'categories': f'=Pareto!$A$2:$A${row}',
        'values':     f'=Pareto!$C$2:$C${row}',
        'name':       '% Acumulada',
        'line':       {'color': '#1A76FF', 'width': 2},
        'marker':     {'type': 'circle', 'size': 5, 'border': {'color': '#1A76FF'}, 'fill': {'color': '#1A76FF'}},
        'y2_axis':    True,
    })
    
    # Combina os dois gráficos em um só
    chart_barra.combine(chart_linha)
    
    # Títulos e legendas
    chart_barra.set_title({'name': 'Diagrama de Pareto'})
    chart_barra.set_x_axis({'name': 'Categorias'})
    chart_barra.set_y_axis({'name': 'Frequência (Absoluta)'})
    chart_linha.set_y2_axis({'name': 'Porcentagem Acumulada', 'max': 1.0, 'num_format': '0%'})
    
    # Insere o gráfico na planilha
    worksheet.insert_chart('E2', chart_barra, {'x_scale': 1.5, 'y_scale': 1.3})
    
    workbook.close()
    output.seek(0)
    return output

# --- INTERFACE DO STREAMLIT ---
st.set_page_config(page_title="Gerador de Pareto", layout="wide")
st.title("📊 Gerador de Gráfico de Pareto Automático (MySQL)")
st.write("Insira sua planilha Excel (.xlsx) com duas colunas: **Categoria** e **Frequência/Custo**.")

uploaded_file = st.file_uploader("Escolha o arquivo Excel", type=["xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_excel(uploaded_file)
        
        if df.shape[1] < 2:
            st.error("A planilha precisa ter pelo menos 2 colunas (Categoria e Valor).")
            st.stop()
            
        df.columns = ['Categoria', 'Frequencia']
        df['Frequencia'] = pd.to_numeric(df['Frequencia'], errors='coerce')
        df = df.dropna().sort_values(by='Frequencia', ascending=False).reset_index(drop=True)
        
        df['Frequencia_Acumulada'] = df['Frequencia'].cumsum()
        total = df['Frequencia'].sum()
        df['Porcentagem_Acumulada'] = (df['Frequencia_Acumulada'] / total) * 100
        
        st.subheader("📋 Dados Processados")
        st.dataframe(df)
        
        # Criação das colunas para colocar os botões lado a lado
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🗄️ Salvar Dados no MySQL", use_container_width=True):
                salvar_dados_mysql(df, uploaded_file.name)
                
        with col2:
            # Gera o arquivo Excel com o gráfico embutido na memória
            excel_data = gerar_excel_pareto(df)
            st.download_button(
                label="📥 Baixar Excel com Gráfico Nativo",
                data=excel_data,
                file_name=f"pareto_{uploaded_file.name}",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        
        # --- GRAFICO DE PARETO (TELA) ---
        st.subheader("📈 Visualização do Gráfico")
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=df['Categoria'], y=df['Frequencia'], 
            name='Frequência', marker_color='rgb(55, 83, 109)'
        ))
        
        fig.add_trace(go.Scatter(
            x=df['Categoria'], y=df['Porcentagem_Acumulada'], 
            name='% Acumulada', yaxis='y2', mode='lines+markers', 
            marker=dict(color='rgb(26, 118, 255)')
        ))
        
        fig.update_layout(
            xaxis=dict(title='Categorias'),
            yaxis=dict(title='Frequência (Absoluta)'),
            yaxis2=dict(
                title='Porcentagem Acumulada (%)',
                title_font=dict(color='rgb(26, 118, 255)'),
                tickfont=dict(color='rgb(26, 118, 255)'),
                overlaying='y', side='right', range=[0, 105]
            ),
            legend=dict(x=0.1, y=1.1, orientation='h')
        )
        
        fig.add_shape(
            type="line", x0=0, x1=len(df)-1, y0=80, y1=80, yref='y2',
            line=dict(color="Red", width=2, dash="dash")
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error(f"Erro ao processar o arquivo: {e}")