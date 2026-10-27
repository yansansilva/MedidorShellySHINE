import streamlit as st
import pandas as pd
import plotly.graph_objs as go
from io import BytesIO
from scipy.integrate import trapz
from zoneinfo import available_timezones

# --- CONSTANTES ---

FUSO_PADRAO = "America/Belem"
FUSOS = sorted(available_timezones()) or [FUSO_PADRAO, "UTC"]


# --- FUNÇÕES DE PROCESSAMENTO (BACKEND) ---

@st.cache_data
def carregar_dados_brutos(uploaded_files):
    """Leitura inicial para identificar estrutura."""
    if not uploaded_files:
        return pd.DataFrame()

    lista_dfs = []
    for file in uploaded_files:
        file.seek(0)
        try:
            df = pd.read_csv(file, sep=';', decimal=',')
            # Se tudo caiu em uma única coluna, o separador provavelmente é vírgula
            if df.shape[1] == 1:
                file.seek(0)
                df = pd.read_csv(file, sep=',')
        except:
            file.seek(0)
            df = pd.read_excel(file)
        lista_dfs.append(df)

    return pd.concat(lista_dfs).reset_index(drop=True) if lista_dfs else pd.DataFrame()


def processar_datetime_custom(df, colunas_tempo, modo="texto", unidade_ts="s", fuso=FUSO_PADRAO):
    """
    Cria a coluna 'TEMPO' a partir das colunas selecionadas pelo usuário.

    modo="texto":     mescla as colunas e interpreta como data/hora (dayfirst).
    modo="timestamp": interpreta UMA coluna como timestamp Unix (UTC) e converte
                      para o fuso horário informado. O resultado é salvo sem
                      informação de fuso (hora local), pois o Excel não suporta
                      datetimes com timezone.
    """
    df = df.copy()
    if not colunas_tempo:
        return df
    try:
        if modo == "timestamp":
            if len(colunas_tempo) != 1:
                st.error("Para timestamp Unix, selecione apenas UMA coluna.")
                return pd.DataFrame()

            valores = pd.to_numeric(df[colunas_tempo[0]], errors='coerce')
            if valores.isna().all():
                st.error("A coluna selecionada não contém valores numéricos de timestamp.")
                return pd.DataFrame()

            tempo = (
                pd.to_datetime(valores, unit=unidade_ts, utc=True)
                .dt.tz_convert(fuso)
                .dt.tz_localize(None)
            )
            df.insert(0, 'TEMPO', tempo)
        elif len(colunas_tempo) > 1:
            string_tempo = df[colunas_tempo].astype(str).agg(' '.join, axis=1)
            df.insert(0, 'TEMPO', pd.to_datetime(string_tempo, dayfirst=True))
        else:
            df.insert(0, 'TEMPO', pd.to_datetime(df[colunas_tempo[0]].astype(str), dayfirst=True))
        return df.drop(columns=colunas_tempo)
    except Exception as e:
        st.error(f"Erro na conversão de tempo: {e}")
        return pd.DataFrame()


def preparar_excel(df):
    """Gera o arquivo Excel em memória para download."""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Dados_Integralizados')
    return output.getvalue()


# --- INTERFACE PRINCIPAL ---

def main():
    st.set_page_config(page_title="Integralização de Dados", layout="wide")
    st.title("📊 Processador de Dados")

    tabs = st.tabs(['📁 1. Configurar Tempo', '⚙️ 2. Processar', '📈 3. Resultados'])

    # --- TAB 1: CARREGAMENTO E TEMPO ---
    with tabs[0]:
        uploaded_files = st.file_uploader("Upload de Arquivos", type=["xlsx", "xls", "csv"], accept_multiple_files=True)

        if uploaded_files:
            df_bruto = carregar_dados_brutos(uploaded_files)
            todas_colunas = df_bruto.columns.tolist()

            st.subheader("Referência Temporal")

            tipo_tempo = st.radio(
                "Formato da referência de tempo:",
                ["Data/hora (texto)", "Timestamp Unix"],
                horizontal=True,
                help="Escolha 'Timestamp Unix' se a coluna de tempo for um número "
                     "(segundos ou milissegundos desde 01/01/1970 UTC)."
            )
            modo = "timestamp" if tipo_tempo == "Timestamp Unix" else "texto"

            unidade_ts, fuso = "s", FUSO_PADRAO
            if modo == "timestamp":
                c_un, c_fuso = st.columns(2)
                unidade_ts = c_un.radio(
                    "Unidade do timestamp:", ["s", "ms"], horizontal=True,
                    help="Segundos (10 dígitos, ex: 1784058360) ou milissegundos (13 dígitos)."
                )
                fuso = c_fuso.selectbox(
                    "Fuso horário do local:",
                    options=FUSOS,
                    index=FUSOS.index(FUSO_PADRAO) if FUSO_PADRAO in FUSOS else 0,
                    help="O timestamp é convertido de UTC para este fuso. Digite para buscar (ex: Belem, Sao_Paulo, UTC)."
                )

            colunas_tempo = st.multiselect(
                "Selecione a(s) coluna(s) que compõem a data/hora:",
                options=todas_colunas,
                help="Ex: Selecione 'Data' e 'Hora' se estiverem separadas em colunas diferentes. "
                     "Para timestamp Unix, selecione apenas a coluna do timestamp."
            )

            if colunas_tempo:
                dados_prontos = processar_datetime_custom(df_bruto, colunas_tempo, modo, unidade_ts, fuso)
                if not dados_prontos.empty:
                    st.success("Tempo configurado com sucesso!")
                    st.dataframe(dados_prontos.head(5))
            else:
                st.info("Aguardando definição das colunas de tempo.")
                dados_prontos = pd.DataFrame()
        else:
            dados_prontos = pd.DataFrame()

    # --- TAB 2: SELEÇÃO E CÁLCULO ---
    if not dados_prontos.empty:
        with tabs[1]:
            st.subheader("Configurações de Integralização")
            col_v1, col_v2 = st.columns(2)

            colunas_analise = [c for c in dados_prontos.columns if c != 'TEMPO']

            with col_v1:
                selecionadas = st.multiselect("Variáveis (Corrente, Tensão, Potência, etc):", options=colunas_analise)
                metodo = st.selectbox('Modo de cálculo:', ['Média', 'Integralização (Trapezoidal)'])

            with col_v2:
                freq_val = st.number_input('Intervalo numérico:', min_value=1, value=15)
                unidade = st.radio('Unidade de tempo:', ['s', 'min', 'h'], horizontal=True)
                excluir = st.checkbox("Remover linhas vazias (NaN)", value=True)

            if selecionadas:
                freq_str = f"{freq_val}{unidade}"
                df_calc = dados_prontos[['TEMPO'] + selecionadas].set_index('TEMPO').sort_index()
                resampler = df_calc.resample(freq_str)

                if "Média" in metodo:
                    df_res = resampler.mean()
                else:
                    df_res = resampler.apply(lambda x: trapz(x) if len(x) > 1 else x.mean())

                if excluir: df_res = df_res.dropna()
                df_res = df_res.reset_index()

                st.divider()
                st.subheader("📥 Download dos Resultados")
                nome_base = st.text_input("Nome do arquivo (sem extensão):", "resultado_processado")

                c_csv, c_xlsx = st.columns(2)

                # Download CSV
                c_csv.download_button(
                    label="Baixar em CSV",
                    data=df_res.to_csv(index=False).encode('utf-8'),
                    file_name=f"{nome_base}.csv",
                    mime='text/csv'
                )

                # Download XLSX
                c_xlsx.download_button(
                    label="Baixar em Excel (XLSX)",
                    data=preparar_excel(df_res),
                    file_name=f"{nome_base}.xlsx",
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )

                st.dataframe(df_res)

        # --- TAB 3: VISUALIZAÇÃO ---
        with tabs[2]:
            if selecionadas:
                escolhas = st.multiselect(
                    "Selecione a(s) variável(is) para o gráfico:",
                    options=selecionadas,
                    default=selecionadas[:1]
                )
                layout_graf = st.radio(
                    "Disposição:",
                    ["Um gráfico por variável", "Todas no mesmo gráfico"],
                    horizontal=True,
                    help="Use gráficos separados quando as variáveis tiverem escalas muito diferentes (ex: tensão e corrente)."
                )

                if not escolhas:
                    st.info("Selecione ao menos uma variável.")
                elif layout_graf == "Um gráfico por variável":
                    for var in escolhas:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(x=dados_prontos['TEMPO'], y=dados_prontos[var], name='Bruto',
                                                 line=dict(color='lightgrey')))
                        fig.add_trace(
                            go.Scatter(x=df_res['TEMPO'], y=df_res[var], name='Processado', line=dict(color='blue')))
                        fig.update_layout(title=var)
                        st.plotly_chart(fig, use_container_width=True, key=f"grafico_{var}")
                else:
                    fig = go.Figure()
                    for var in escolhas:
                        fig.add_trace(go.Scatter(x=dados_prontos['TEMPO'], y=dados_prontos[var], name=f'{var} (bruto)',
                                                 opacity=0.35))
                        fig.add_trace(go.Scatter(x=df_res['TEMPO'], y=df_res[var], name=f'{var} (processado)'))
                    st.plotly_chart(fig, use_container_width=True, key="grafico_conjunto")


if __name__ == "__main__":
    main()