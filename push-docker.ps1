# Tag images for Docker Hub
Write-Host "Tagging images for user krvatsal..."
docker tag meshworks-backend krvatsal/meshworks-backend:latest
docker tag meshworks-frontend krvatsal/meshworks-frontend:latest

# Push to Docker Hub
Write-Host "Pushing krvatsal/meshworks-backend:latest..."
docker push krvatsal/meshworks-backend:latest

Write-Host "Pushing krvatsal/meshworks-frontend:latest..."
docker push krvatsal/meshworks-frontend:latest

Write-Host "All images pushed successfully!"
