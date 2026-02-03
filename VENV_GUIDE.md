# How to Use Virtual Environment

## Activate Virtual Environment

### PowerShell:
```powershell
cd C:\Users\prend\IdeaProjects\honest-congress
.\venv\Scripts\Activate.ps1
```

If you get execution policy error, run:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Command Prompt (cmd):
```cmd
cd C:\Users\prend\IdeaProjects\honest-congress
.\venv\Scripts\activate.bat
```

## Verify Activation

After activation, your prompt should show `(venv)` at the beginning:
```
(venv) PS C:\Users\prend\IdeaProjects\honest-congress>
```

Check Python location:
```powershell
which python
# or
(Get-Command python).Source
```

Should show: `C:\Users\prend\IdeaProjects\honest-congress\venv\Scripts\python.exe`

## Run Commands

Once activated, use commands directly:
```bash
# Without venv prefix
python -m src.cli parse --limit 100
python -m src.cli analyze
python -m src.cli serve --port 8001
```

## Deactivate

To exit venv:
```bash
deactivate
```

## Alternative: Always Use Full Path

If activation doesn't work, always use full path:
```powershell
.\venv\Scripts\python.exe -m src.cli parse --limit 100
```

## Install Missing Packages

If you encounter "ModuleNotFoundError":
```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

Or install specific package:
```powershell
.\venv\Scripts\pip.exe install sqlalchemy
```

