#!/usr/bin/env bash
#SBATCH --job-name=pbc-xlns-mcpp
#SBATCH --output=results/slurm-%A_%a.out
#SBATCH --error=results/slurm-%A_%a.err
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=12:00:00

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${CONFIG:-${PROJECT_ROOT}/configs/main_time_120x10.yaml}"
: "${SLURM_ARRAY_TASK_ID:?Submit this script as a Slurm array after preparing tasks}"

export PYTHONHASHSEED=0
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

cd "${PROJECT_ROOT}"
python scripts/run_experiments.py \
  --config "${CONFIG}" \
  --task-index "${SLURM_ARRAY_TASK_ID}" \
  --workers 1 \
  --no-aggregate \
  --no-download
