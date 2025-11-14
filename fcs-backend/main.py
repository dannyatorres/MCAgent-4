from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fcs_analyzer import FCSAnalyzer

app = FastAPI(title="FCS Analyzer API", version="1.0.0")

# Enable CORS so your frontend can call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize analyzer
analyzer = FCSAnalyzer()

# Request model
class FCSRequest(BaseModel):
    fcs_text: str
    additional_withhold: float = 10.0

# Health check endpoint
@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "FCS Analyzer API is running",
        "version": "1.0.0"
    }

# Main analysis endpoint
@app.post("/api/analyze")
def analyze_fcs(request: FCSRequest):
    """
    Analyze an FCS report

    Args:
        fcs_text: Full FCS report text
        additional_withhold: Additional withholding capacity % (default 10)

    Returns:
        Complete analysis with withholding, term analysis, and affordable funding
    """
    try:
        result = analyzer.analyze(
            fcs_text=request.fcs_text,
            additional_withhold=request.additional_withhold
        )

        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['error'])

        return result

    except Exception as e:
        import traceback
        print("=" * 50)
        print("ERROR IN ANALYZE:")
        print(traceback.format_exc())
        print("=" * 50)
        raise HTTPException(status_code=500, detail=str(e))

# Reload lender profiles endpoint (for live updates)
@app.post("/api/reload-profiles")
def reload_profiles():
    """Reload lender profiles from JSON file"""
    try:
        analyzer.profiles = analyzer._load_profiles('config/lender_profiles.json')
        return {
            "status": "success",
            "message": "Lender profiles reloaded",
            "profile_count": len(analyzer.profiles)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get all lender profiles
@app.get("/api/lenders")
def get_lenders():
    """Get all lender profiles"""
    return analyzer.profiles

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
