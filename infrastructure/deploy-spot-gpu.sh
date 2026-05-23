#!/bin/bash
# Deploy Spot GPU Worker for Saramsa ML Pipeline
#
# This script deploys a Spot VM with NVIDIA T4 GPU for accelerated ML inference.
# Cost: ~$40-110/month (70-90% cheaper than regular GPU)
# Processing: 5-8x faster than CPU (200 comments in 2-3 min vs 16 min)
#
# Prerequisites:
#   - Azure CLI installed and logged in
#   - Docker image pushed to ACR: saramsa.azurecr.io/saramsa-celery-gpu:latest

set -e

# Configuration
RESOURCE_GROUP="saramsa"
LOCATION="eastus"
VM_NAME="saramsa-gpu-worker-spot"
VM_SIZE="Standard_NC4as_T4_v3"  # 4 vCPU, 28GB RAM, NVIDIA T4 GPU
IMAGE="Ubuntu2204"
ACR_NAME="saramsa"
CONTAINER_IMAGE="saramsa.azurecr.io/saramsa-celery-gpu:latest"

# Environment variables (fetch from existing celery worker)
echo "📋 Fetching environment variables from existing worker..."
ENV_VARS=$(az webapp config appsettings list \
    --name saramsa-celery-prod-2 \
    --resource-group $RESOURCE_GROUP \
    --query "[].{name:name, value:value}" -o json)

# Extract critical env vars
REDIS_URL=$(echo $ENV_VARS | jq -r '.[] | select(.name=="REDIS_URL") | .value')
DATABASE_URL=$(echo $ENV_VARS | jq -r '.[] | select(.name=="DATABASE_URL") | .value')
OPENAI_API_KEY=$(echo $ENV_VARS | jq -r '.[] | select(.name=="OPENAI_API_KEY") | .value')

echo "✅ Environment variables fetched"

# Create Spot VM
echo "🚀 Creating Spot GPU VM: $VM_NAME"
az vm create \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --location $LOCATION \
    --size $VM_SIZE \
    --image $IMAGE \
    --priority Spot \
    --max-price 0.15 \
    --eviction-policy Deallocate \
    --admin-username azureuser \
    --generate-ssh-keys \
    --public-ip-address "" \
    --nsg "" \
    --assign-identity \
    --tags "Environment=Production" "Component=GPU-Worker" "CostCenter=ML-Inference"

echo "✅ Spot VM created: $VM_NAME"

# Install NVIDIA drivers and Docker
echo "📦 Installing NVIDIA drivers and Docker..."
az vm run-command invoke \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --command-id RunShellScript \
    --scripts \
        "curl -fsSL https://get.docker.com -o get-docker.sh" \
        "sh get-docker.sh" \
        "distribution=\$(. /etc/os-release;echo \$ID\$VERSION_ID)" \
        "curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | apt-key add -" \
        "curl -s -L https://nvidia.github.io/nvidia-docker/\$distribution/nvidia-docker.list | tee /etc/apt/sources.list.d/nvidia-docker.list" \
        "apt-get update && apt-get install -y nvidia-docker2" \
        "systemctl restart docker"

echo "✅ NVIDIA Docker installed"

# Login to ACR and pull image
echo "🐳 Pulling GPU worker image from ACR..."
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

az vm run-command invoke \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --command-id RunShellScript \
    --scripts \
        "docker login ${ACR_NAME}.azurecr.io -u ${ACR_NAME} -p ${ACR_PASSWORD}" \
        "docker pull ${CONTAINER_IMAGE}"

echo "✅ Image pulled"

# Start GPU worker container
echo "🎯 Starting GPU Celery worker..."
az vm run-command invoke \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --command-id RunShellScript \
    --scripts \
        "docker run -d \
            --name saramsa-gpu-worker \
            --restart unless-stopped \
            --gpus all \
            -e REDIS_URL='${REDIS_URL}' \
            -e DATABASE_URL='${DATABASE_URL}' \
            -e OPENAI_API_KEY='${OPENAI_API_KEY}' \
            -e NLI_BACKEND=gpu \
            -e NLI_BATCH_SIZE=128 \
            -e ENABLE_GPU_ROUTING=true \
            -e USE_LOCAL_PIPELINE=true \
            ${CONTAINER_IMAGE}"

echo "✅ GPU worker started"

# Verify GPU availability
echo "🔍 Verifying GPU..."
az vm run-command invoke \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --command-id RunShellScript \
    --scripts "docker exec saramsa-gpu-worker nvidia-smi"

echo ""
echo "========================================="
echo "✅ SPOT GPU WORKER DEPLOYED SUCCESSFULLY"
echo "========================================="
echo ""
echo "VM Name: $VM_NAME"
echo "VM Size: $VM_SIZE (NVIDIA T4)"
echo "Pricing: ~\$0.10-0.15/hour (Spot)"
echo "Queue: gpu_ml_tasks"
echo ""
echo "To monitor:"
echo "  az vm run-command invoke --resource-group $RESOURCE_GROUP --name $VM_NAME --command-id RunShellScript --scripts 'docker logs saramsa-gpu-worker --tail 50'"
echo ""
echo "To check GPU usage:"
echo "  az vm run-command invoke --resource-group $RESOURCE_GROUP --name $VM_NAME --command-id RunShellScript --scripts 'nvidia-smi'"
echo ""
echo "⚠️  Note: Spot VMs can be evicted. Tasks will automatically retry on CPU worker."
echo "========================================="
