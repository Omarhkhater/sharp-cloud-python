# Create folders
$folders = @(
    ".github/workflows",
    "docs",
    "tests",
    "scripts",
    "sharp_cloud",
    "sharp_cloud/auth",
    "sharp_cloud/api",
    "sharp_cloud/protocol",
    "sharp_cloud/models",
    "sharp_cloud/devices"
)

foreach ($folder in $folders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}

# Create files
$files = @(
    ".gitignore",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "sharp_cloud/__init__.py",
    "sharp_cloud/client.py",
    "sharp_cloud/exceptions.py"
)

foreach ($file in $files) {
    if (!(Test-Path $file)) {
        New-Item -ItemType File -Path $file | Out-Null
    }
}

Write-Host ""
Write-Host "✅ Project structure created successfully!" -ForegroundColor Green