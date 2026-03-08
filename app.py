import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai
import os

# =================================================================
# 1. CONFIGURACIÓN DE PÁGINA Y ESTILO
# =================================================================
st.set_page_config(page_title="BioSteam Web Simulator", layout="wide")
st.title("🧪 Simulador Interactivo: Planta de Etanol")
st.markdown("---")

# =================================================================
# 2. BARRA LATERAL (ENTRADAS DINÁMICAS)
# =================================================================
st.sidebar.header("Parámetros de Entrada")

f_etanol = st.sidebar.slider("Flujo Etanol (kg/hr)", 10, 500, 100)
f_agua = st.sidebar.slider("Flujo Agua (kg/hr)", 500, 1500, 900)
t_alimentacion = st.sidebar.number_input("Temp. Alimentación (°C)", value=25)
p_bomba = st.sidebar.slider("Presión Bomba (atm)", 1.0, 10.0, 4.0)
t_flash = st.sidebar.slider("Temp. Calentador W-220 (°C)", 70, 98, 92)

# =================================================================
# 3. LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# =================================================================
def run_simulation(f_eth, f_wat, t_in, p_atm, t_set):
    # ELIMINAR IDs DUPLICADOS: Limpia el flowsheet global antes de cada corrida
    bst.main_flowsheet.clear() 
    
    # Configuración de compuestos
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes
    mosto = bst.Stream("1-MOSTO", Water=f_wat, Ethanol=f_eth, units="kg/hr", 
                       T=t_in+273.15, P=101325)
    
    vinazas_retorno = bst.Stream("Vinazas-Retorno", Water=200, Ethanol=0, units="kg/hr",
                               T=95+273.15, P=300000)

    # Equipos
    P100 = bst.Pump("P-100", ins=mosto, P=p_atm*101325)
    
    W210 = bst.HXprocess("W-210", ins=(P100-0, vinazas_retorno), 
                         outs=("3-Mosto-Pre","Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15

    W220 = bst.HXutility("W-220", ins=W210-0, outs="Mezcla", T=t_set+273.15)
    
    V100 = bst.IsenthalpicValve("V-100", ins=W220-0, outs="Mezcla-Bifásica", P=101325)
    
    V1 = bst.Flash("V-1", ins=V100-0, outs=("Vapor-caliente", "Vinazas"), P=101325, Q=0)
    
    W310 = bst.HXutility("W-310", ins=V1-0, outs="Producto-Final", T=25+273.15)
    
    P200 = bst.Pump("P-200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Sistema
    sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    
    try:
        sys.simulate()
        return sys, None
    except Exception as e:
        return None, str(e)

# =================================================================
# 4. GENERACIÓN DE REPORTES (CORRECCIÓN FLASH DUTY)
# =================================================================
def generar_reporte(sistema):
    # Tabla de Materia
    datos_mat = []
    for s in sistema.streams:
        if s.F_mass > 0.001:
            datos_mat.append({
                "ID": s.ID,
                "Temp (°C)": round(s.T - 273.15, 2),
                "Flujo (kg/h)": round(s.F_mass, 2),
                "% Etanol": f"{s.imass['Ethanol']/s.F_mass:.1%}" if s.F_mass > 0 else "0%"
            })
    df_mat = pd.DataFrame(datos_mat)

    # Tabla de Energía (Manejo de errores específicos)
    datos_en = []
    for u in sistema.units:
        calor_kw = 0.0
        # Caso Flash: Cálculo manual por entalpía (evita error .duty)
        if isinstance(u, bst.Flash):
            calor_kw = (sum(s.H for s in u.outs) - sum(s.H for s in u.ins)) / 3600
        # Caso HX Utility
        elif hasattr(u, 'duty') and u.duty is not None:
            calor_kw = u.duty / 3600
        # Caso HX Process (Recuperación)
        elif isinstance(u, bst.HXprocess):
            calor_kw = (u.outs[0].H - u.ins[0].H) / 3600

        if abs(calor_kw) > 0.01:
            datos_en.append({"Equipo": u.ID, "Carga Térmica (kW)": round(calor_kw, 2)})
            
    return df_mat, pd.DataFrame(datos_en)

# =================================================================
# 5. INTEGRACIÓN CON GEMINI
# =================================================================
def consultar_ia(df_m, df_e):
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        Como experto en ingeniería química, analiza estos datos:
        MATERIA: {df_m.to_markdown()}
        ENERGÍA: {df_e.to_markdown()}
        
        Dame 3 puntos clave sobre la eficiencia de este proceso y una sugerencia técnica. 
        Sé conciso y profesional.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return "Error al conectar con el Tutor IA. Verifica tu API Key en Secrets."

# =================================================================
# 6. EJECUCIÓN Y UI
# =================================================================
if st.button("🚀 Ejecutar Simulación"):
    resultado_sys, error = run_simulation(f_etanol, f_agua, t_alimentacion, p_bomba, t_flash)
    
    if error:
        st.error(f"Error en la simulación: {error}")
    else:
        st.success("¡Simulación convergida exitosamente!")
        
        col1, col2 = st.columns(2)
        df_m, df_e = generar_reporte(resultado_sys)
        
        with col1:
            st.subheader("Balances de Materia")
            st.dataframe(df_m, use_container_width=True)
            
        with col2:
            st.subheader("Cargas Térmicas")
            st.dataframe(df_e, use_container_width=True)
        
        # Diagrama de Flujo (DFP)
        st.subheader("Diagrama del Proceso")
        try:
            resultado_sys.diagram(file="diagrama", format="png")
            st.image("diagrama.png")
        except:
            st.warning("No se pudo renderizar el diagrama (Graphviz no detectado).")

        # Tutor IA
        st.markdown("---")
        st.subheader("🤖 Análisis del Tutor IA")
        with st.spinner("Consultando a Gemini..."):
            analisis = consultar_ia(df_m, df_e)
            st.write(analisis)

else:
    st.info("Configura los parámetros a la izquierda y presiona 'Ejecutar'.")
