# Uso: source env.sh   (desde la raíz del repositorio, una vez por terminal)
#
# Deja disponibles las herramientas de simulación/síntesis y define PIPELINEC_DIR,
# la ruta de tu clon de https://github.com/JulianKemmerer/PipelineC
# (cámbiala exportando PIPELINEC_DIR antes de hacer source).
export PIPELINEC_DIR="${PIPELINEC_DIR:-$HOME/Documents/tfg/PipelineC}"
export OSS_CAD_SUITE="${OSS_CAD_SUITE:-$HOME/oss-cad/oss-cad-suite}"
export PATH="$HOME/.local/bin:$OSS_CAD_SUITE/bin:$PATH"
