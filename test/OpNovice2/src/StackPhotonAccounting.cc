#include "StackPhotonAccounting.hh"

#include "DetectorConstruction.hh"
#include "Run.hh"

#include "G4AnalysisManager.hh"
#include "G4Event.hh"
#include "G4OpBoundaryProcess.hh"
#include "G4OpticalPhoton.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryVertex.hh"
#include "G4ProcessManager.hh"
#include "G4Step.hh"
#include "G4StepPoint.hh"
#include "G4SystemOfUnits.hh"
#include "G4Track.hh"
#include "G4VPhysicalVolume.hh"
#include "G4VProcess.hh"

#include <algorithm>
#include <limits>

namespace {
G4ThreadLocal G4int eventTree = -1;
G4ThreadLocal G4int layerTree = -1;
G4ThreadLocal G4int sensorTree = -1;
G4ThreadLocal G4int flowTree = -1;

G4bool Optical(const G4Track* track)
{
  return track && track->GetDefinition() == G4OpticalPhoton::OpticalPhotonDefinition();
}

void WriteRow(G4int tree, const std::vector<G4int>& integers,
              const std::vector<G4double>& doubles = {},
              const std::vector<G4int>& trailingIntegers = {})
{
  auto* analysis = G4AnalysisManager::Instance();
  G4int column = 0;
  for (const auto value : integers) analysis->FillNtupleIColumn(tree, column++, value);
  for (const auto value : doubles) analysis->FillNtupleDColumn(tree, column++, value);
  for (const auto value : trailingIntegers) analysis->FillNtupleIColumn(tree, column++, value);
  analysis->AddNtupleRow(tree);
}
}

StackPhotonAccounting::StackPhotonAccounting(const DetectorConstruction& detector)
  : fLayerCount(detector.GetStackLayerCount()),
    fSensorsPerLayer(detector.GetActiveSensorsPerLayer()),
    fCopyStride(detector.GetSensorCopyStride()),
    fLayers(static_cast<std::size_t>(fLayerCount))
{
  if (detector.GetWorld()) fVolumes.emplace(detector.GetWorld(), Source{kWorld, -1, -1});
  const auto& tiles = detector.GetStackTiles();
  const auto& steel = detector.GetStackAbsorbers();
  for (std::size_t layer = 0; layer < tiles.size(); ++layer) {
    fVolumes.emplace(tiles[layer], Source{kTile, static_cast<G4int>(layer), -1});
  }
  for (std::size_t layer = 0; layer < steel.size(); ++layer) {
    fVolumes.emplace(steel[layer], Source{kSteel, static_cast<G4int>(layer), -1});
  }
  for (const auto* sensor : detector.GetSiPMs()) {
    const auto copy = sensor->GetCopyNo();
    SensorRecord record;
    record.copy = copy;
    record.layer = detector.GetSensorLayer(copy);
    record.local = detector.GetSensorLocalIndex(copy);
    fVolumes.emplace(sensor, Source{kSiPM, record.layer, copy});
    fSensors.push_back(record);
  }
  std::sort(fSensors.begin(), fSensors.end(), [](const auto& a, const auto& b) { return a.copy < b.copy; });
  for (std::size_t index = 0; index < fSensors.size(); ++index) {
    fSensorByCopy.emplace(fSensors[index].copy, index);
  }
}

void StackPhotonAccounting::BookNtuples()
{
  if (eventTree >= 0) return;
  auto* analysis = G4AnalysisManager::Instance();
  eventTree = analysis->CreateNtuple("stack_event_v2", "Independent optical event balance");
  for (const auto* name : {
      "run_id", "event_id", "layers", "sensors_per_layer", "sensor_copy_stride",
      "generated_legacy", "detected_legacy", "births_total", "births_primary",
      "births_nonoptical_parent", "births_optical_parent", "births_scintillation",
      "births_cerenkov", "births_wls", "births_wls2", "births_other", "births_tile",
      "births_steel", "births_world", "births_sipm", "births_unknown", "tracks_started",
      "tracks_finalized", "pending_births", "unmatched_starts", "duplicate_births",
      "duplicate_starts", "duplicate_finalizations", "unresolved_tracks", "parent_mismatches",
      "unknown_birth_origins", "unknown_root_origins", "inactive_sensor_hits", "duplicate_steps",
      "fate_sipm", "fate_bulk_tile", "fate_bulk_steel", "fate_bulk_world", "fate_bulk_other",
      "fate_boundary_absorption", "fate_boundary_detection", "fate_world_escape", "fate_wls",
      "fate_wls2", "fate_no_rindex", "fate_other", "boundary_technical_steps", "primary_optical_started"}) {
    analysis->CreateNtupleIColumn(eventTree, name);
  }
  for (const auto* name : {"tile_nonoptical_edep_mev", "tile_optical_edep_mev", "legacy_tile_edep_mev",
                          "steel_nonoptical_edep_mev", "steel_optical_edep_mev", "legacy_steel_edep_mev"}) {
    analysis->CreateNtupleDColumn(eventTree, name);
  }
  analysis->CreateNtupleIColumn(eventTree, "boundary_no_rindex");
  analysis->CreateNtupleIColumn(eventTree, "collected_step_no_rindex");
  analysis->FinishNtuple(eventTree);

  layerTree = analysis->CreateNtuple("stack_layer_v2", "Normalized stack layer events");
  for (const auto* name : {
      "run_id", "event_id", "layer", "births_total", "births_primary", "births_nonoptical_parent",
      "births_optical_parent", "births_scintillation", "births_cerenkov", "births_wls", "births_wls2",
      "births_other", "generated_legacy", "scintillation_legacy", "cerenkov_legacy",
      "detected_all_origins", "detected_same_root_layer", "detected_same_birth_layer",
      "charged_tile_entry_count", "electron_tile_entry_count", "proton_tile_entry_count",
      "other_charged_tile_entry_count", "primary_neutron_tile_entry_valid",
      "primary_neutron_elastic_count", "primary_neutron_inelastic_count", "primary_neutron_capture_count"}) {
    analysis->CreateNtupleIColumn(layerTree, name);
  }
  for (const auto* name : {
      "tile_nonoptical_edep_mev", "tile_optical_edep_mev", "legacy_tile_edep_mev",
      "steel_nonoptical_edep_mev", "steel_optical_edep_mev", "legacy_steel_edep_mev",
      "charged_tile_entry_ke_mev", "primary_neutron_tile_entry_x_mm",
      "primary_neutron_tile_entry_y_mm", "primary_neutron_tile_entry_z_mm"}) {
    analysis->CreateNtupleDColumn(layerTree, name);
  }
  analysis->FinishNtuple(layerTree);

  sensorTree = analysis->CreateNtuple("stack_sensor_v2", "Active stack sensor events");
  for (const auto* name : {
      "run_id", "event_id", "layer", "local_sensor", "global_copy", "detected_all_origins",
      "detected_same_root_layer", "detected_same_birth_layer", "detected_root_outside",
      "detected_root_unknown", "detected_birth_outside", "detected_birth_unknown"}) {
    analysis->CreateNtupleIColumn(sensorTree, name);
  }
  analysis->FinishNtuple(sensorTree);

  flowTree = analysis->CreateNtuple("stack_photon_flow_v2", "Sparse optical birth-to-terminal flows");
  for (const auto* name : {"run_id", "event_id", "root_class", "root_layer", "root_sensor",
                          "birth_class", "birth_layer", "birth_sensor"}) {
    analysis->CreateNtupleIColumn(flowTree, name);
  }
  analysis->CreateNtupleSColumn(flowTree, "creator_process");
  for (const auto* name : {"parent_optical", "fate", "sensor_copy", "photon_count"}) {
    analysis->CreateNtupleIColumn(flowTree, name);
  }
  analysis->FinishNtuple(flowTree);
}

void StackPhotonAccounting::BirthCounts::Add(const Birth& birth)
{
  ++total;
  if (birth.primary) { ++primary; return; }
  if (birth.opticalParent) ++opticalParent;
  else ++nonOpticalParent;
  if (birth.creator == "Scintillation") ++scintillation;
  else if (birth.creator == "Cerenkov") ++cerenkov;
  else if (birth.creator == "OpWLS") ++wls;
  else if (birth.creator == "OpWLS2") ++wls2;
  else ++other;
}

void StackPhotonAccounting::BeginEvent()
{
  fPendingBirths.clear();
  fTracks.clear();
  fNonOpticalLastStep.clear();
  fFlows.clear();
  fBirths = BirthCounts{};
  fBirthVolumes.fill(0);
  fFates.fill(0);
  std::fill(fLayers.begin(), fLayers.end(), LayerRecord{});
  for (auto& sensor : fSensors) {
    const auto layer = sensor.layer, local = sensor.local, copy = sensor.copy;
    sensor = SensorRecord{};
    sensor.layer = layer; sensor.local = local; sensor.copy = copy;
  }
  fStarted = fFinalized = fExpectedPrimaryBirths = fPrimaryStarted = 0;
  fUnmatchedStarts = fDuplicateBirths = fDuplicateStarts = fDuplicateFinalizations = 0;
  fParentMismatches = fUnknownBirthOrigins = fUnknownRootOrigins = 0;
  fInactiveSensorHits = fDuplicateSteps = fBoundaryTechnical = 0;
  fBoundaryNoRindex = fCollectedStepNoRindex = 0;
  fWritten = false;
}

void StackPhotonAccounting::ObservePrimaries(const G4Event* event)
{
  if (!event) return;
  for (G4int index = 0; index < event->GetNumberOfPrimaryVertex(); ++index) {
    for (auto* particle = event->GetPrimaryVertex(index)->GetPrimary(); particle; particle = particle->GetNext()) {
      if (particle->GetG4code() == G4OpticalPhoton::OpticalPhotonDefinition()) ++fExpectedPrimaryBirths;
    }
  }
}

StackPhotonAccounting::Source StackPhotonAccounting::Locate(const G4VPhysicalVolume* volume) const
{
  const auto found = fVolumes.find(volume);
  return found == fVolumes.end() ? Source{} : found->second;
}

void StackPhotonAccounting::RegisterBirth(const Birth& birth)
{
  fBirths.Add(birth);
  ++fBirthVolumes[static_cast<std::size_t>(birth.location.volumeClass)];
  if (birth.location.volumeClass == kUnknown) ++fUnknownBirthOrigins;
  if (birth.root.volumeClass == kUnknown) ++fUnknownRootOrigins;
  if (birth.location.volumeClass == kTile && birth.location.layer >= 0 && birth.location.layer < fLayerCount) {
    fLayers[static_cast<std::size_t>(birth.location.layer)].births.Add(birth);
  }
}

void StackPhotonAccounting::ObserveSecondaryBirths(const G4Step* step)
{
  const auto* parent = step->GetTrack();
  const G4bool parentOptical = Optical(parent);
  const auto* children = step->GetSecondaryInCurrentStep();
  for (const auto* child : *children) {
    if (!Optical(child)) continue;
    Birth birth;
    birth.parentID = parent->GetTrackID();
    birth.opticalParent = parentOptical;
    const auto* creator = child->GetCreatorProcess();
    birth.creator = creator ? creator->GetProcessName() : "unassigned";
    // Geant4 optical constructors normally retain the pre-step touchable.
    // A missing child touchable falls back to the observed generating volume;
    // this still identifies the actual birth medium, never its ancestor.
    birth.location = Locate(child->GetVolume() ? child->GetVolume() : step->GetPreStepPoint()->GetPhysicalVolume());
    if (parentOptical) {
      const auto found = fTracks.find(parent->GetTrackID());
      birth.root = found == fTracks.end() ? Source{} : found->second.birth.root;
    }
    else birth.root = birth.location;
    RegisterBirth(birth);
    if (!fPendingBirths.emplace(child, birth).second) ++fDuplicateBirths;
  }
}

void StackPhotonAccounting::BeginTracking(const G4Track* track)
{
  if (!Optical(track)) return;
  auto& record = fTracks[track->GetTrackID()];
  if (record.started) {
    if (record.finalized) ++fDuplicateStarts;
    return; // A suspended track can resume without being born again.
  }
  record.started = true;
  ++fStarted;
  if (track->GetParentID() == 0) {
    record.birth.primary = true;
    record.birth.creator = "primary";
    record.birth.parentID = 0;
    record.birth.location = Locate(track->GetVolume());
    record.birth.root = record.birth.location;
    RegisterBirth(record.birth);
    ++fPrimaryStarted;
    return;
  }
  const auto found = fPendingBirths.find(track);
  if (found == fPendingBirths.end()) {
    ++fUnmatchedStarts;
    record.birth.creator = "unmatched";
    record.birth.parentID = track->GetParentID();
    return;
  }
  record.birth = found->second;
  if (record.birth.parentID != track->GetParentID()) ++fParentMismatches;
  fPendingBirths.erase(found); // Pointer reuse after this track dies is safe.
}

G4OpBoundaryProcess* StackPhotonAccounting::BoundaryProcess()
{
  if (fBoundarySearched) return fBoundary;
  fBoundarySearched = true;
  auto* processes = G4OpticalPhoton::OpticalPhotonDefinition()->GetProcessManager()->GetPostStepProcessVector(typeDoIt);
  for (G4int index = 0; index < processes->entries(); ++index) {
    if (auto* boundary = dynamic_cast<G4OpBoundaryProcess*>((*processes)[index])) { fBoundary = boundary; break; }
  }
  return fBoundary;
}

void StackPhotonAccounting::ObserveStep(const G4Step* step)
{
  const auto* track = step->GetTrack();
  const G4bool optical = Optical(track);
  const auto stepNumber = track->GetCurrentStepNumber();
  TrackRecord* opticalRecord = nullptr;
  if (optical) {
    opticalRecord = &fTracks[track->GetTrackID()];
    if (opticalRecord->lastStep == stepNumber) { ++fDuplicateSteps; return; }
    opticalRecord->lastStep = stepNumber;
  }
  else {
    auto inserted = fNonOpticalLastStep.emplace(track->GetTrackID(), stepNumber);
    if (!inserted.second) {
      if (inserted.first->second == stepNumber) { ++fDuplicateSteps; return; }
      inserted.first->second = stepNumber;
    }
  }
  // Deliberately before the existing SiPM early return: even that final step
  // may have created secondaries, all of which belong in the birth ledger.
  ObserveSecondaryBirths(step);
  const auto location = Locate(step->GetPreStepPoint()->GetPhysicalVolume());
  const auto energy = step->GetTotalEnergyDeposit();
  if (energy > 0. && location.layer >= 0 && location.layer < fLayerCount) {
    auto& layer = fLayers[static_cast<std::size_t>(location.layer)];
    if (location.volumeClass == kTile) {
      if (optical) layer.tileOpticalEdep += energy;
      else layer.tileNonOpticalEdep += energy;
    }
    else if (location.volumeClass == kSteel) {
      if (optical) layer.steelOpticalEdep += energy;
      else layer.steelNonOpticalEdep += energy;
    }
  }
  if (!opticalRecord) return;
  const auto* post = step->GetPostStepPoint();
  const auto* process = post->GetProcessDefinedStep();
  opticalRecord->process = process ? process->GetProcessName() : "";
  opticalRecord->preVolume = location;
  opticalRecord->boundaryStatus = -1;
  opticalRecord->worldEscape = post->GetStepStatus() == fWorldBoundary && !post->GetPhysicalVolume();
  if (post->GetStepStatus() == fGeomBoundary) {
    const auto* boundary = BoundaryProcess();
    opticalRecord->boundaryStatus = boundary ? boundary->GetStatus() : Undefined;
    const auto status = opticalRecord->boundaryStatus;
    if (status == NoRINDEX) ++fBoundaryNoRindex;
    if (status == Undefined || status == NotAtBoundary || status == SameMaterial || status == StepTooSmall) {
      ++fBoundaryTechnical;
    }
  }
}

void StackPhotonAccounting::ObserveSiPMCollection(const G4Step* step)
{
  auto& record = fTracks[step->GetTrack()->GetTrackID()];
  // Preserve the legacy entry-counting rule, but never let its terminal
  // precedence hide a NoRINDEX that occurred during this same SiPM step.
  if (record.boundaryStatus == NoRINDEX) ++fCollectedStepNoRindex;
  record.collected = true;
  record.collectedSensor = step->GetPreStepPoint()->GetPhysicalVolume()->GetCopyNo();
  const auto found = fSensorByCopy.find(record.collectedSensor);
  if (found == fSensorByCopy.end()) { ++fInactiveSensorHits; return; }
  auto& sensor = fSensors[found->second];
  ++sensor.all;
  auto& layer = fLayers[static_cast<std::size_t>(sensor.layer)];
  ++layer.detectedAll;
  const auto& root = record.birth.root;
  const auto& birth = record.birth.location;
  if (root.volumeClass == kTile && root.layer == sensor.layer) { ++sensor.sameRoot; ++layer.detectedSameRoot; }
  if (birth.volumeClass == kTile && birth.layer == sensor.layer) { ++sensor.sameBirth; ++layer.detectedSameBirth; }
  if (root.volumeClass == kUnknown) ++sensor.rootUnknown;
  else if (root.volumeClass != kTile) ++sensor.rootOutside;
  if (birth.volumeClass == kUnknown) ++sensor.birthUnknown;
  else if (birth.volumeClass != kTile) ++sensor.birthOutside;
}

void StackPhotonAccounting::EndTracking(const G4Track* track)
{
  if (!Optical(track)) return;
  if (track->GetTrackStatus() != fStopAndKill && track->GetTrackStatus() != fKillTrackAndSecondaries) return;
  auto& record = fTracks[track->GetTrackID()];
  if (record.finalized) { ++fDuplicateFinalizations; return; }
  G4int fate = kOtherTermination;
  if (record.collected) fate = kCollected;
  else if (record.boundaryStatus == NoRINDEX) fate = kNoRindex;
  else if (record.process == "OpAbsorption") {
    fate = record.preVolume.volumeClass == kTile ? kBulkTile :
           record.preVolume.volumeClass == kSteel ? kBulkSteel :
           record.preVolume.volumeClass == kWorld ? kBulkWorld : kBulkOther;
  }
  else if (record.boundaryStatus == Absorption) fate = kBoundaryAbsorption;
  else if (record.boundaryStatus == Detection) fate = kBoundaryDetection;
  else if (record.worldEscape) fate = kWorldEscape;
  else if (record.process == "OpWLS") fate = kWLS;
  else if (record.process == "OpWLS2") fate = kWLS2;
  record.finalized = true;
  ++fFinalized;
  ++fFates[static_cast<std::size_t>(fate)];
  const auto& birth = record.birth;
  ++fFlows[FlowKey{birth.root.volumeClass, birth.root.layer, birth.root.sensor,
                    birth.location.volumeClass, birth.location.layer, birth.location.sensor,
                    birth.creator, birth.opticalParent ? 1 : 0, fate,
                    record.collected ? record.collectedSensor : -1}];
}

void StackPhotonAccounting::WriteEvent(const Run& run)
{
  if (fWritten) return;
  fWritten = true;
  const auto runID = run.GetRunID();
  const auto eventID = run.GetCurrentEventID();
  G4int unresolved = 0;
  for (const auto& track : fTracks) if (!track.second.finalized) ++unresolved;
  const G4int primaryDifference = fExpectedPrimaryBirths - fPrimaryStarted;
  if (primaryDifference < 0) fUnmatchedStarts += -primaryDifference;
  const G4int pending = static_cast<G4int>(fPendingBirths.size()) + std::max(0, primaryDifference);
  std::vector<G4int> integers = {
    runID, eventID, fLayerCount, fSensorsPerLayer, fCopyStride,
    run.GetEventGeneratedOpticalCount(), run.GetEventSiPMDetectionCount(), fBirths.total,
    fBirths.primary, fBirths.nonOpticalParent, fBirths.opticalParent, fBirths.scintillation,
    fBirths.cerenkov, fBirths.wls, fBirths.wls2, fBirths.other,
    fBirthVolumes[kTile], fBirthVolumes[kSteel], fBirthVolumes[kWorld], fBirthVolumes[kSiPM], fBirthVolumes[kUnknown],
    fStarted, fFinalized, pending, fUnmatchedStarts, fDuplicateBirths, fDuplicateStarts,
    fDuplicateFinalizations, unresolved, fParentMismatches, fUnknownBirthOrigins, fUnknownRootOrigins,
    fInactiveSensorHits, fDuplicateSteps
  };
  integers.insert(integers.end(), fFates.begin(), fFates.end());
  integers.push_back(fBoundaryTechnical);
  integers.push_back(fPrimaryStarted);
  G4double tileNonOptical = 0., tileOptical = 0., steelNonOptical = 0., steelOptical = 0.;
  for (const auto& layer : fLayers) {
    tileNonOptical += layer.tileNonOpticalEdep; tileOptical += layer.tileOpticalEdep;
    steelNonOptical += layer.steelNonOpticalEdep; steelOptical += layer.steelOpticalEdep;
  }
  WriteRow(eventTree, integers, {tileNonOptical / MeV, tileOptical / MeV, run.GetEventTileEnergyDeposit() / MeV,
                                 steelNonOptical / MeV, steelOptical / MeV, run.GetEventSteelEnergyDeposit() / MeV},
                                 {fBoundaryNoRindex, fCollectedStepNoRindex});
  const auto nan = std::numeric_limits<G4double>::quiet_NaN();
  for (G4int index = 0; index < fLayerCount; ++index) {
    const auto& layer = fLayers[static_cast<std::size_t>(index)];
    const auto& birth = layer.births;
    const auto& legacy = run.GetEventStackLayer(index);
    const auto& entry = legacy.primaryNeutronTileEntryPosition;
    WriteRow(layerTree, {
      runID, eventID, index, birth.total, birth.primary, birth.nonOpticalParent, birth.opticalParent,
      birth.scintillation, birth.cerenkov, birth.wls, birth.wls2, birth.other,
      legacy.generatedOptical, legacy.scintillation, legacy.cerenkov,
      layer.detectedAll, layer.detectedSameRoot, layer.detectedSameBirth,
      legacy.chargedEntryCount, legacy.electronEntryCount, legacy.protonEntryCount, legacy.otherChargedEntryCount,
      legacy.primaryNeutronTileEntryValid ? 1 : 0, legacy.neutronElasticCount, legacy.neutronInelasticCount,
      legacy.neutronCaptureCount
    }, {
      layer.tileNonOpticalEdep / MeV, layer.tileOpticalEdep / MeV, legacy.tileEnergyDeposit / MeV,
      layer.steelNonOpticalEdep / MeV, layer.steelOpticalEdep / MeV, legacy.steelEnergyDeposit / MeV,
      legacy.chargedEntryKineticEnergy / MeV,
      legacy.primaryNeutronTileEntryValid ? entry.x() / mm : nan,
      legacy.primaryNeutronTileEntryValid ? entry.y() / mm : nan,
      legacy.primaryNeutronTileEntryValid ? entry.z() / mm : nan
    });
  }
  for (const auto& sensor : fSensors) {
    WriteRow(sensorTree, {runID, eventID, sensor.layer, sensor.local, sensor.copy,
                          sensor.all, sensor.sameRoot, sensor.sameBirth, sensor.rootOutside,
                          sensor.rootUnknown, sensor.birthOutside, sensor.birthUnknown});
  }
  auto* analysis = G4AnalysisManager::Instance();
  for (const auto& entry : fFlows) {
    const auto& key = entry.first;
    const std::array<G4int, 8> source = {{runID, eventID, std::get<0>(key), std::get<1>(key), std::get<2>(key),
                                         std::get<3>(key), std::get<4>(key), std::get<5>(key)}};
    for (G4int column = 0; column < 8; ++column) analysis->FillNtupleIColumn(flowTree, column, source[column]);
    analysis->FillNtupleSColumn(flowTree, 8, std::get<6>(key));
    analysis->FillNtupleIColumn(flowTree, 9, std::get<7>(key));
    analysis->FillNtupleIColumn(flowTree, 10, std::get<8>(key));
    analysis->FillNtupleIColumn(flowTree, 11, std::get<9>(key));
    analysis->FillNtupleIColumn(flowTree, 12, entry.second);
    analysis->AddNtupleRow(flowTree);
  }
}
