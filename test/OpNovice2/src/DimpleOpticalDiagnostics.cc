#include "DimpleOpticalDiagnostics.hh"

#include "DetectorConstruction.hh"
#include "G4AnalysisManager.hh"
#include "G4Event.hh"
#include "G4Exception.hh"
#include "G4OpticalPhoton.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryVertex.hh"
#include "G4Step.hh"
#include "G4SystemOfUnits.hh"
#include "G4Track.hh"
#include "G4VProcess.hh"

#include <array>
#include <limits>
#include <unordered_set>

namespace
{
  struct EventCounts
  {
    // Non-optical parent Scintillation/Cerenkov/other, then optical parent.
    std::array<G4int, 6> births{};
    G4int allBirths = 0;
    G4int opticalPrimaries = 0;
    G4int trackingResumes = 0;
    G4int detectionCalls = 0;
    G4int duplicateDetections = 0;
    G4int unregisteredDetections = 0;
    std::unordered_set<G4int> started;
    std::unordered_set<G4int> detected;
  };

  struct State
  {
    G4bool enabled = false;
    G4int ntuple = -1;
    G4int eventID = -1;
    G4bool eventWritten = false;
    EventCounts event;
  };

  thread_local State state;

  void RequireEvent(G4int eventID)
  {
    if (state.eventID != eventID || state.eventWritten) {
      G4Exception("DimpleOpticalDiagnostics", "DimpleOptical_002", FatalException,
                  "Missing event reset or duplicate diagnostic event row.");
    }
  }
}

void DimpleOpticalDiagnostics::BeginRun(const DetectorConstruction* detector)
{
  state.enabled = detector && detector->IsDimpleOpticalDiagnosticsEnabled();
  if (!state.enabled) return;
  if (detector->IsStackEnabled() || detector->IsAbsorberEnabled() ||
      detector->GetSiPMLayout() != "single") {
    G4Exception("DimpleOpticalDiagnostics::BeginRun", "DimpleOptical_001", FatalException,
                "Dimple optical diagnostics require one tile, single SiPM, and no absorber.");
  }
  // This must run before OpenFile; the constructor books only the legacy trees.
  if (state.ntuple >= 0) return;
  auto* analysis = G4AnalysisManager::Instance();
  state.ntuple = analysis->CreateNtuple("dimple_optical_event_v1",
                                      "Passive optical births and unique starts; not terminal closure");
  for (const auto* name : {
         "event_id", "event_aborted", "legacy_generated", "legacy_detected",
         "nonoptical_parent_scintillation_births", "nonoptical_parent_cerenkov_births",
         "nonoptical_parent_other_births", "optical_parent_scintillation_births",
         "optical_parent_cerenkov_births", "optical_parent_other_births",
         "all_optical_births", "optical_primary_tracks", "optical_tracks_started",
         "optical_tracking_resumes", "sipm_detection_calls", "sipm_unique_detected_tracks",
         "duplicate_sipm_detections", "unregistered_sipm_detections",
         "primary_count", "primary_pdg"}) {
    analysis->CreateNtupleIColumn(state.ntuple, name);
  }
  for (const auto* name : {"primary_dir_x", "primary_dir_y", "primary_dir_z",
                           "primary_kinetic_energy_mev"}) {
    analysis->CreateNtupleDColumn(state.ntuple, name);
  }
  analysis->FinishNtuple(state.ntuple);
}

void DimpleOpticalDiagnostics::BeginEvent(const G4Event* event)
{
  if (!state.enabled) return;
  state.event = EventCounts{};
  state.eventID = event ? event->GetEventID() : -1;
  state.eventWritten = false;
}

void DimpleOpticalDiagnostics::ObserveStep(const G4Step* step)
{
  if (!state.enabled) return;
  const auto* optical = G4OpticalPhoton::OpticalPhotonDefinition();
  const G4bool opticalParent = step->GetTrack()->GetDefinition() == optical;
  const auto* secondaries = step->GetSecondaryInCurrentStep();
  for (const auto* child : *secondaries) {
    if (child->GetDefinition() != optical) continue;
    const auto* creator = child->GetCreatorProcess();
    const G4String name = creator ? creator->GetProcessName() : "";
    const G4int process = name == "Scintillation" ? 0 : name == "Cerenkov" ? 1 : 2;
    ++state.event.births[(opticalParent ? 3 : 0) + process];
    ++state.event.allBirths;
  }
}

void DimpleOpticalDiagnostics::ObserveTrack(const G4Track* track)
{
  if (!state.enabled || track->GetDefinition() != G4OpticalPhoton::OpticalPhotonDefinition()) return;
  if (state.event.started.insert(track->GetTrackID()).second) {
    if (track->GetParentID() == 0) ++state.event.opticalPrimaries;
  }
  else {
    // PreTracking is called again after suspension; it is not another birth.
    ++state.event.trackingResumes;
  }
}

void DimpleOpticalDiagnostics::ObserveDetection(const G4Track* track)
{
  if (!state.enabled) return;
  ++state.event.detectionCalls;
  if (!state.event.detected.insert(track->GetTrackID()).second) ++state.event.duplicateDetections;
  if (state.event.started.count(track->GetTrackID()) == 0) ++state.event.unregisteredDetections;
}

void DimpleOpticalDiagnostics::EndEvent(const G4Event* event, G4int legacyGenerated,
                                        G4int legacyDetected)
{
  if (!state.enabled) return;
  RequireEvent(event->GetEventID());
  G4int primaryCount = 0;
  const G4PrimaryParticle* first = nullptr;
  for (G4int vertexIndex = 0; vertexIndex < event->GetNumberOfPrimaryVertex(); ++vertexIndex) {
    const auto* vertex = event->GetPrimaryVertex(vertexIndex);
    for (G4int index = 0; index < vertex->GetNumberOfParticle(); ++index) {
      const auto* primary = vertex->GetPrimary(index);
      if (!first) first = primary;
      ++primaryCount;
    }
  }
  const auto& c = state.event;
  const std::array<G4int, 20> values{{
    event->GetEventID(), event->IsAborted() ? 1 : 0, legacyGenerated, legacyDetected,
    c.births[0], c.births[1], c.births[2], c.births[3], c.births[4], c.births[5],
    c.allBirths, c.opticalPrimaries, static_cast<G4int>(c.started.size()),
    c.trackingResumes, c.detectionCalls, static_cast<G4int>(c.detected.size()),
    c.duplicateDetections, c.unregisteredDetections, primaryCount,
    first ? first->GetPDGcode() : 0}};
  auto* analysis = G4AnalysisManager::Instance();
  for (G4int column = 0; column < static_cast<G4int>(values.size()); ++column) {
    analysis->FillNtupleIColumn(state.ntuple, column, values[column]);
  }
  const auto missing = std::numeric_limits<G4double>::quiet_NaN();
  const auto direction = first ? first->GetMomentumDirection() : G4ThreeVector();
  analysis->FillNtupleDColumn(state.ntuple, 20, first ? direction.x() : missing);
  analysis->FillNtupleDColumn(state.ntuple, 21, first ? direction.y() : missing);
  analysis->FillNtupleDColumn(state.ntuple, 22, first ? direction.z() : missing);
  analysis->FillNtupleDColumn(state.ntuple, 23, first ? first->GetKineticEnergy() / MeV : missing);
  analysis->AddNtupleRow(state.ntuple);
  state.eventWritten = true;
}
