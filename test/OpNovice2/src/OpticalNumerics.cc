#include "OpticalNumerics.hh"
#include "DetectorConstruction.hh"
#include "PaintedCornerBoundary.hh"
#include "G4Event.hh"
#include "G4GenericMessenger.hh"
#include "G4GeometryTolerance.hh"
#include "G4LogicalBorderSurface.hh"
#include "G4LogicalVolume.hh"
#include "G4Navigator.hh"
#include "G4TransportationManager.hh"
#include "G4OpBoundaryProcess.hh"
#include "G4OpticalPhoton.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryVertex.hh"
#include "G4ProcessManager.hh"
#include "G4Step.hh"
#include "G4SystemOfUnits.hh"
#include "G4Track.hh"
#include "G4VPhysicalVolume.hh"
#include "Randomize.hh"
#include <cmath>
#include <iomanip>
#include <sstream>

namespace {
void Vector(std::ostream& out, const G4ThreeVector& v) { out << '[' << v.x() << ',' << v.y() << ',' << v.z() << ']'; }
void Volume(std::ostream& out, const G4VPhysicalVolume* v) {
  out << "[\"" << (v?v->GetName():"outside") << "\"," << (v?v->GetCopyNo():-1) << ']';
}
}
OpticalNumerics& OpticalNumerics::Instance() { static OpticalNumerics instance; return instance; }
void OpticalNumerics::Fail(const G4String& text) {
  G4Exception("OpticalNumerics", "PaintedCorner_Failure", FatalException, text);
}
void OpticalNumerics::InstallMessenger() {
  if (fMessenger) return;
  fMessenger = std::make_unique<G4GenericMessenger>(this,"/opnovice2/numerics/","Versioned v2 transport candidate");
  fMessenger->DeclareProperty("mode",fMode).SetCandidates("legacy painted-corner-v1 painted-corner-v2").SetStates(G4State_PreInit);
  fMessenger->DeclareProperty("cornerScale",fScale).SetParameterName("cornerScale",false).SetRange("cornerScale == 0 || cornerScale == 16 || cornerScale == 32 || cornerScale == 64").SetStates(G4State_PreInit);
  fMessenger->DeclareProperty("probeFile",fProbeFile,"Explicit optical diagnostic table; never a neutron production source").SetStates(G4State_PreInit);
}
void OpticalNumerics::Validate(const DetectorConstruction& detector) const {
  if (fMode != "legacy" && fMode != "painted-corner-v1" && fMode != "painted-corner-v2") Fail("Unknown numerical profile");
  if ((fMode == "legacy" && fScale != 0) || (fMode != "legacy" && fScale != 16 && fScale != 32 && fScale != 64))
    Fail("Candidate scale must be explicitly 16, 32 or 64; legacy requires scale zero");
  if ((fMode != "legacy" || IsProbe()) && (!detector.IsStackV2() || detector.IsW08PhotonDiagnosticsEnabled()))
    Fail("Numerical candidate/probes require stack v2 and forbid W08 diagnostics");
  if ((fMode != "legacy" || IsProbe()) && G4RunManager::GetRunManager()->GetRunManagerType() != G4RunManager::sequentialRM)
    Fail("Numerical candidate/probes require Serial");
}
void OpticalNumerics::WriteIdentity(std::ostream& out) const {
  out << "{\"profile\":\"" << fMode << "\",\"scale\":" << fScale
      << ",\"surface_tolerance_mm\":" << std::setprecision(17)
      << G4GeometryTolerance::GetInstance()->GetSurfaceTolerance()/mm
      << ",\"diagnostic_primary\":" << (IsProbe()?"true":"false")
      << ",\"promotion\":\"candidate-not-certified-by-executable\"}";
}
void OpticalNumerics::BeginRun(const G4String& output) {
  fDetector = static_cast<const DetectorConstruction*>(G4RunManager::GetRunManager()->GetUserDetectorConstruction());
  Validate(*fDetector);
  fEnabled = fDetector->IsStackV2();
  if (!fEnabled) return;
  fProbes.clear();
  if (IsProbe()) {
    std::ifstream input(fProbeFile); G4String line;
    if (!input) Fail("Cannot read diagnostic probe table");
    while (std::getline(input,line)) {
      if (line.empty() || line[0]=='#') continue;
      Probe p; p.seed[2]=0;
      std::istringstream row(line);
      row >> p.id >> p.seed[0] >> p.seed[1];
      for (auto* v : {&p.position,&p.direction,&p.polarization}) for (int a=0;a<3;++a) row >> (*v)[a];
      row >> p.energy;
      G4String extra;
      if (!row || (row >> extra) || p.seed[0]<=0 || p.seed[1]<=0 || !std::isfinite(p.energy) || p.energy<=0
          || std::abs(p.direction.mag2()-1.)>1.e-12 || std::abs(p.polarization.mag2()-1.)>1.e-12
          || std::abs(p.direction.dot(p.polarization))>1.e-12) Fail("Malformed deterministic probe row");
      for (int a=0;a<3;++a) if (!std::isfinite(p.position[a]) || !std::isfinite(p.direction[a]) || !std::isfinite(p.polarization[a])) Fail("Nonfinite probe vector");
      p.position *= mm; p.energy *= eV;
      fProbes.push_back(p);
    }
    if (fProbes.empty()) Fail("Empty probe table");
  }
  G4String stem=output; if (stem.size()>=5 && stem.substr(stem.size()-5)==".root") stem.erase(stem.size()-5);
  fOutput.open(stem+".optical-numerics.jsonl");
  if (!fOutput) Fail("Cannot write numerical audit");
  fOutput << std::setprecision(17) << std::boolalpha << std::unitbuf;
  auto* pm=G4OpticalPhoton::OpticalPhotonDefinition()->GetProcessManager();
  auto* processes=pm->GetPostStepProcessVector(typeDoIt);
  int count=0;
  fOutput << "{\"kind\":\"run\",\"identity\":"; WriteIdentity(fOutput);
  fOutput << ",\"post_step_order\":[";
  for (int i=0;i<processes->entries();++i) {
    auto* process=(*processes)[i];
    if (i) fOutput << ',';
    fOutput << "[\"" << (process?process->GetProcessName():"null") << "\"," << (process?pm->GetProcessActivation(process):false) << ']';
    if (auto* boundary=dynamic_cast<G4OpBoundaryProcess*>(process)) { fBoundary=boundary; ++count; }
  }
  fOutput << "],\"boundary_class\":\"" << (dynamic_cast<PaintedCornerBoundary*>(fBoundary)?"PaintedCornerBoundary":"G4OpBoundaryProcess") << "\"}\n";
  if (count!=1 || !pm->GetProcessActivation(fBoundary)) Fail("Exactly one active OpBoundary is required");
  if ((dynamic_cast<PaintedCornerBoundary*>(fBoundary)!=nullptr) != (fMode!="legacy")) Fail("Installed process disagrees with requested profile");
}
void OpticalNumerics::EndRun() { if (fOutput.is_open()) { fOutput << "{\"kind\":\"run_end\"}\n"; fOutput.close(); } fEnabled=false; }
G4bool OpticalNumerics::GenerateProbe(G4Event* event) {
  if (!IsProbe()) return false;
  const auto id=static_cast<std::size_t>(event->GetEventID());
  if (!fEnabled || id>=fProbes.size()) { Fail("Probe event outside registered table"); return true; }
  const auto& p=fProbes[id];
  G4Random::setTheSeeds(p.seed); // Explicit paired input, before primary generation.
  auto* vertex=new G4PrimaryVertex(p.position,0.);
  auto* particle=new G4PrimaryParticle(G4OpticalPhoton::OpticalPhotonDefinition());
  particle->SetKineticEnergy(p.energy); particle->SetMomentumDirection(p.direction);
  particle->SetPolarization(p.polarization.x(),p.polarization.y(),p.polarization.z());
  vertex->SetPrimary(particle); event->AddPrimaryVertex(vertex);
  return true;
}
void OpticalNumerics::BeginEvent(const G4Event* event) {
  if (!fEnabled) return;
  fEvent=event->GetEventID(); fTracks.clear(); fImmediate.clear(); fCorrections=fNoRindex=fEscapes=0;
  if (IsProbe()) {
    fOutput << "{\"kind\":\"probe\",\"event\":" << fEvent << ",\"id\":\"" << fProbes.at(fEvent).id << "\"}\n";
  }
}
void OpticalNumerics::Correction(const G4Track& track,const G4Step& step,G4int reflected,
 const G4ThreeVector& before,const G4ThreeVector& after,G4int faces,G4int tile,G4double tolerance) {
  ++fCorrections;
  fTracks[track.GetTrackID()].logged=true;
  fOutput << "{\"kind\":\"correction\",\"event\":" << fEvent << ",\"track\":" << track.GetTrackID()
          << ",\"reflection_step\":" << reflected << ",\"step\":" << track.GetCurrentStepNumber()
          << ",\"raw_status\":13,\"faces\":" << faces << ",\"tile\":" << tile
          << ",\"tolerance_mm\":" << tolerance/mm << ",\"scale\":" << fScale << ",\"before_mm\":";
  Vector(fOutput,before/mm); fOutput << ",\"after_mm\":"; Vector(fOutput,after/mm);
  fOutput << ",\"displacement_mm\":" << (after-before).mag()/mm << ",\"pre\":";
  Volume(fOutput,step.GetPreStepPoint()->GetPhysicalVolume()); fOutput << ",\"post\":";
  Volume(fOutput,step.GetPostStepPoint()->GetPhysicalVolume()); fOutput << ",\"next\":";
  Volume(fOutput,track.GetNextTouchableHandle()->GetVolume());
  fOutput << ",\"same_volume_navigator_verified\":true,\"particle_change_modified_fields\":[\"position\"]}\n";
}
void OpticalNumerics::ImmediateCorrection(const G4Track& track,const G4Step& step,
 const G4ThreeVector& position,G4int faces,G4double tolerance) {
  if (!fImmediate.emplace(track.GetTrackID(),Immediate{track.GetCurrentStepNumber(),faces,tolerance,
      step.GetPostStepPoint()->GetPosition(),position,step.GetPreStepPoint()->GetPhysicalVolume(),
      step.GetPostStepPoint()->GetPhysicalVolume()}).second) Fail("Unverified or repeated immediate correction");
}
void OpticalNumerics::ObserveStep(const G4Step* step) {
  if (!fEnabled || step->GetTrack()->GetDefinition()!=G4OpticalPhoton::OpticalPhotonDefinition()) return;
  const auto* track=step->GetTrack(); auto& record=fTracks[track->GetTrackID()];
  const auto* pre=step->GetPreStepPoint(); const auto* post=step->GetPostStepPoint();
  const auto number=track->GetCurrentStepNumber();
  const auto status=post->GetStepStatus()==fGeomBoundary ? static_cast<int>(fBoundary->GetStatus()) : -1;
  record.status=status;
  auto* prePV=pre->GetPhysicalVolume(); auto* postPV=post->GetPhysicalVolume();
  auto physicalPosition=post->GetPosition();
  const auto pending=fImmediate.find(track->GetTrackID());
  if (pending!=fImmediate.end()) {
    const auto r=pending->second;
    auto* navigator=G4TransportationManager::GetTransportationManager()->GetNavigatorForTracking();
    CornerTransportation* transport=nullptr;
    auto* processes=G4OpticalPhoton::OpticalPhoton()->GetProcessManager()->GetProcessList();
    for(int i=0;i<processes->entries();++i)
      if (auto* p=dynamic_cast<CornerTransportation*>((*processes)[i])) transport=p;
    auto* logical=r.tile->GetLogicalVolume();
    if (r.step!=number || status!=SpikeReflection || postPV!=r.tile || track->GetNextVolume()!=r.tile
        || navigator->CreateTouchableHistoryHandle()->GetVolume()!=r.tile
        || !transport || transport->CachedVolume()!=r.tile || post->GetPosition()!=r.after
        || post->GetMaterial()!=logical->GetMaterial()
        || post->GetMaterialCutsCouple()!=logical->GetMaterialCutsCouple()
        || post->GetSensitiveDetector()!=logical->GetSensitiveDetector()) Fail("Applied immediate correction failed volume/material/cache consistency");
    ++fCorrections;record.logged=true;
    fOutput << "{\"kind\":\"correction\",\"event\":" << fEvent << ",\"track\":" << track->GetTrackID()
      << ",\"reflection_step\":" << number << ",\"step\":" << number << ",\"raw_status\":7,\"faces\":" << r.faces
      << ",\"tile\":" << r.tile->GetCopyNo() << ",\"tolerance_mm\":" << r.tolerance/mm << ",\"scale\":" << fScale
      << ",\"before_mm\":";Vector(fOutput,r.before/mm);fOutput << ",\"after_mm\":";Vector(fOutput,r.after/mm);
    fOutput << ",\"displacement_mm\":" << (r.after-r.before).mag()/mm << ",\"pre\":";Volume(fOutput,prePV);
    fOutput << ",\"raw_post\":";Volume(fOutput,r.rawPost);fOutput << ",\"post\":";Volume(fOutput,postPV);
    fOutput << ",\"next\":";Volume(fOutput,track->GetNextVolume());
    fOutput << ",\"same_volume_navigator_verified\":true,\"transport_cache_verified\":true,\"physical_proposals_delegated_unchanged\":true,"
      << "\"particle_change_modified_fields\":[\"position\",\"geometry_state\"]}\n";
    postPV=r.rawPost;physicalPosition=r.before; // Preserve the physical boundary in the diagnostic sequence.
    fImmediate.erase(pending);
  }
  const auto* process=post->GetProcessDefinedStep(); record.process=process?process->GetProcessName():"none";
  record.volume=prePV?prePV->GetName():"outside"; record.copy=prePV?prePV->GetCopyNo():-1;
  if (prePV && prePV->GetName()=="SiPM") record.sensor=prePV->GetCopyNo();
  if (status==NoRINDEX) ++fNoRindex;
  G4bool edge=false;
  if (status==SpikeReflection && fDetector->GetTileLayer(prePV)>=0) {
    auto* border=G4LogicalBorderSurface::GetSurface(prePV,postPV);
    auto* surface=border?dynamic_cast<G4OpticalSurface*>(border->GetSurfaceProperty()):nullptr;
    if (surface==fDetector->GetSurface() && surface->GetFinish()==polishedfrontpainted) {
      const auto local=physicalPosition-prePV->GetTranslation();
      const auto half=fDetector->GetTileHalfSize(); const auto tau=G4GeometryTolerance::GetInstance()->GetSurfaceTolerance();
      int near=0; for(int a=0;a<3;++a) if(std::abs(std::abs(local[a])-half[a])<=tau) ++near;
      if(near>=2) {edge=true;record.edgeStep=number;record.tile=prePV->GetCopyNo();}
    }
  }
  const bool leak=(status==StepTooSmall && number>record.edgeStep && number<=record.edgeStep+2
      && fDetector->GetTileLayer(prePV)==record.tile && postPV!=prePV);
  if(leak) ++fEscapes;
  if ((IsProbe() && status>=0) || edge || (number<=record.edgeStep+2) || status==NoRINDEX) {
    record.logged=true;
    fOutput << "{\"kind\":\"boundary\",\"event\":" << fEvent << ",\"track\":" << track->GetTrackID()
      << ",\"parent\":" << track->GetParentID() << ",\"step\":" << number << ",\"status\":" << status
      << ",\"edge_reflection\":" << edge << ",\"painted_zero_step_escape\":" << leak
      << ",\"length_mm\":" << step->GetStepLength()/mm << ",\"pre\":"; Volume(fOutput,prePV);
    fOutput << ",\"post\":"; Volume(fOutput,postPV);
    fOutput << ",\"position_mm\":"; Vector(fOutput,physicalPosition/mm);
    fOutput << ",\"direction\":"; Vector(fOutput,post->GetMomentumDirection());
    fOutput << ",\"polarization\":"; Vector(fOutput,post->GetPolarization());
    fOutput << ",\"energy_ev\":" << post->GetKineticEnergy()/eV << ",\"time_ns\":" << post->GetGlobalTime()/ns << '}';
    fOutput << '\n';
  }
}
void OpticalNumerics::EndTracking(const G4Track* track) {
  if (!fEnabled || track->GetDefinition()!=G4OpticalPhoton::OpticalPhotonDefinition()
      || (track->GetTrackStatus()!=fStopAndKill && track->GetTrackStatus()!=fKillTrackAndSecondaries)) return;
  auto found=fTracks.find(track->GetTrackID()); if(found==fTracks.end()) { Fail("Missing numerical track observation");return; }
  const auto& r=found->second;
  if (IsProbe() || r.logged) {
    fOutput << "{\"kind\":\"terminal\",\"event\":" << fEvent << ",\"track\":" << track->GetTrackID()
      << ",\"parent\":" << track->GetParentID() << ",\"sensor\":" << r.sensor << ",\"process\":\"" << r.process
      << "\",\"status\":" << r.status << ",\"volume\":[\"" << r.volume << "\"," << r.copy << "]}\n";
  }
  fTracks.erase(found);
}
void OpticalNumerics::EndEvent() {
  if (!fEnabled) return;
  if (!fImmediate.empty()) Fail("Immediate corrections remained unverified at event end");
  fOutput << "{\"kind\":\"event_end\",\"event\":" << fEvent << ",\"corrections\":" << fCorrections
          << ",\"boundary_no_rindex\":" << fNoRindex << ",\"painted_zero_step_escapes\":" << fEscapes
          << ",\"unsettled\":" << fTracks.size();
  if (IsProbe()) {
    std::ostringstream state; G4Random::getTheEngine()->put(state);
    // Serialize the engine without drawing. JSON-escape its newlines and quotes.
    fOutput << ",\"rng_end\":\"";
    for (char c:state.str()) { if(c=='\n') fOutput << "\\n"; else if(c=='\"'||c=='\\') fOutput << '\\' << c; else fOutput << c; }
    fOutput << '\"';
  }
  fOutput << "}\n";
}
