#include "W08PhotonDiagnostics.hh"

#include "G4AnalysisManager.hh"
#include "G4Event.hh"
#include "G4GeometryTolerance.hh"
#include "G4OpBoundaryProcess.hh"
#include "G4OpticalPhoton.hh"
#include "G4ProcessManager.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryVertex.hh"
#include "G4Step.hh"
#include "G4StepPoint.hh"
#include "G4SystemOfUnits.hh"
#include "G4Track.hh"
#include "G4VProcess.hh"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <sstream>

namespace {
G4ThreadLocal G4int eventNtuple = -1;
G4ThreadLocal G4int terminalNtuple = -1;
G4ThreadLocal G4int boundaryNtuple = -1;

G4bool IsOptical(const G4Track* track)
{
  return track && track->GetDefinition() == G4OpticalPhoton::OpticalPhotonDefinition();
}

G4bool IsTechnical(G4int status)
{
  return status == Undefined || status == NotAtBoundary ||
         status == SameMaterial || status == StepTooSmall;
}

std::string AddressString(const G4Track* track)
{
  std::ostringstream value;
  value << static_cast<const void*>(track);
  return value.str();
}
}

W08PhotonDiagnostics::W08PhotonDiagnostics(const W08GeometrySnapshot& geometry)
  : fGeometry(geometry),
    fRegionTolerance(10. * G4GeometryTolerance::GetInstance()->GetSurfaceTolerance())
{
  const char* auditPath = std::getenv("W08_BIRTH_AUDIT_FILE");
  if (!auditPath || !*auditPath) return;
  fBirthAudit.open(auditPath);
  if (!fBirthAudit) {
    G4cerr << "Cannot open W08_BIRTH_AUDIT_FILE: " << auditPath << G4endl;
    return;
  }
  fBirthAudit << "kind\tevent_id\tbirth_serial\taddress\ttrack_id\tparent_track_id\tparent_particle"
                "\tparent_step\tcreator\tstep_process\tstep_status\tedep_mev\tprocess_index"
                "\tprocess_name\tprocess_active\tnonopt_births\topt_births\tstarted"
                "\tlegacy_generated\tpending\tunmatched_binds\tduplicate_births"
                "\tparent_mismatches\tlinked_starts\n";
  const auto* manager = G4OpticalPhoton::OpticalPhotonDefinition()->GetProcessManager();
  if (manager) {
    const auto* processes = manager->GetProcessList();
    for (G4int index = 0; index < manager->GetProcessListLength(); ++index) {
      auto* process = (*processes)[index];
      if (!process) continue;
      std::array<std::string, 24> row{};
      row[0] = "Process";
      row[12] = std::to_string(index);
      row[13] = process->GetProcessName();
      row[14] = manager->GetProcessActivation(process) ? "1" : "0";
      WriteBirthAuditRow(row);
    }
  }
  fBirthAudit.flush();
}

void W08PhotonDiagnostics::BookNtuples()
{
  // Serial action construction can precede execution of pre-init UI commands.
  // Book at BeginOfRun, after the flag is resolved, once per analysis thread.
  if (eventNtuple >= 0) return;
  auto analysis = G4AnalysisManager::Instance();
  eventNtuple = analysis->CreateNtuple("w08_photon_event_v1", "W08 event photon balance");
  for (const auto* name : {
         "run_id", "event_id", "generated_reference", "optical_started", "optical_finalized",
         "sipm_collected", "tile_bulk_absorption", "air_bulk_absorption", "other_bulk_absorption",
         "boundary_absorption", "boundary_detection", "world_escape", "wls_conversion",
         "other_termination", "unresolved", "duplicate_finalizations", "unknown_regions",
         "ambiguous_boundaries", "invalid_normals", "boundary_technical", "unsupported_births",
         "sipm_front", "sipm_back", "sipm_xminus", "sipm_xplus", "sipm_yminus", "sipm_yplus",
         "sipm_edge_unknown"}) {
    analysis->CreateNtupleIColumn(eventNtuple, name);
  }
  analysis->CreateNtupleDColumn(eventNtuple, "tile_nonoptical_edep_mev");
  analysis->CreateNtupleDColumn(eventNtuple, "tile_optical_edep_mev");
  analysis->CreateNtupleIColumn(eventNtuple, "ambiguous_photons_unique");
  analysis->FinishNtuple(eventNtuple);

  terminalNtuple = analysis->CreateNtuple("w08_photon_terminal_v1", "W08 terminal photon counts");
  for (const auto* name : {"run_id", "event_id", "fate", "last_exit_region", "ever_dimple", "photon_count"}) {
    analysis->CreateNtupleIColumn(terminalNtuple, name);
  }
  analysis->FinishNtuple(terminalNtuple);

  boundaryNtuple = analysis->CreateNtuple("w08_photon_boundary_v1", "W08 boundary interactions by event");
  for (const auto* name : {"run_id", "event_id", "interface", "region", "direction", "status", "angle_bin", "interactions"}) {
    analysis->CreateNtupleIColumn(boundaryNtuple, name);
  }
  analysis->FinishNtuple(boundaryNtuple);
}

void W08PhotonDiagnostics::BeginEvent()
{
  fTracks.clear();
  fBoundaryCounts.clear();
  fTerminalCounts.clear();
  fFates.fill(0);
  fSiPMFaces.fill(0);
  fStarted = fFinalized = fDuplicateFinalizations = 0;
  fUnknownRegions = fAmbiguousBoundaries = fInvalidNormals = fBoundaryTechnical = 0;
  fUnsupportedBirths = fSourceIsElectron ? 0 : 1;
  fNonOpticalEnergyDeposit = fOpticalEnergyDeposit = 0.;
  fEventWritten = false;
  fPendingBirths.clear();
  fBirthAuditEventID = -1;
  fNextBirthSerial = fNonOpticalParentBirths = fOpticalParentBirths = 0;
  fUnmatchedBirthBindings = fDuplicateBirths = fBirthParentMismatches = fLinkedStarts = 0;
}

void W08PhotonDiagnostics::ObservePrimary(const G4Event* event)
{
  fBirthAuditEventID = event ? event->GetEventID() : -1;
  // Read the generated event as well as the pre-run GPS configuration: this
  // catches a primary-generator override and the actual sampling weights.
  G4bool valid = event && event->GetNumberOfPrimaryVertex() == 1;
  if (valid) {
    const auto* vertex = event->GetPrimaryVertex(0);
    const auto position = vertex->GetPosition();
    valid = vertex->GetNumberOfParticle() == 1 && vertex->GetWeight() == 1. &&
            std::abs(position.z() - 4. * mm) <= 1.e-9 * mm &&
            std::abs(position.x() - fGeometry.tileCenter.x()) <= fGeometry.tileHalfSize.x() &&
            std::abs(position.y() - fGeometry.tileCenter.y()) <= fGeometry.tileHalfSize.y();
    const auto* primary = vertex->GetPrimary();
    valid = valid && primary && primary->GetPDGcode() == 11 &&
            primary->GetWeight() == 1. &&
            std::abs(primary->GetKineticEnergy() - 1. * MeV) <= 1.e-12 * MeV &&
            (primary->GetMomentumDirection() - G4ThreeVector(0., 0., -1.)).mag2() <= 1.e-24;
  }
  if (!valid) ++fUnsupportedBirths;
}

void W08PhotonDiagnostics::BeginTracking(const G4Track* track)
{
  if (!IsOptical(track)) return;
  auto& record = fTracks[track->GetTrackID()];
  // PreTracking is also called when suspended tracks resume. Neither lifetime
  // state nor counts are inherited from the parent's TrackInformation.
  if (record.started) return;
  record.started = true;
  ++fStarted;
  if (fBirthAudit.is_open()) BindBirth(track);
  const auto* creator = track->GetCreatorProcess();
  if (!creator || creator->GetProcessName() != "Scintillation" ||
      track->GetVolume() != fGeometry.tilePV || track->GetWeight() != 1.) {
    ++fUnsupportedBirths;
  }
}

void W08PhotonDiagnostics::WriteBirthAuditRow(const std::array<std::string, 24>& values)
{
  for (std::size_t column = 0; column < values.size(); ++column) {
    if (column) fBirthAudit << '\t';
    // Standard Geant4 names contain no delimiters, but retain a valid TSV if
    // an unexpected user-defined process name contains one.
    for (const char character : values[column]) {
      if (character == '\t') fBirthAudit << "\\t";
      else if (character == '\n') fBirthAudit << "\\n";
      else if (character == '\r') fBirthAudit << "\\r";
      else fBirthAudit << character;
    }
  }
  fBirthAudit << '\n';
}

void W08PhotonDiagnostics::ObserveBirths(const G4Step* step)
{
  const auto* parent = step->GetTrack();
  const auto* children = step->GetSecondaryInCurrentStep();
  const auto* stepProcess = step->GetPostStepPoint()->GetProcessDefinedStep();
  for (const auto* child : *children) {
    const auto* creator = child->GetCreatorProcess();
    if (!IsOptical(child) || !creator || creator->GetProcessName() != "Scintillation") continue;
    if (IsOptical(parent)) ++fOpticalParentBirths;
    else ++fNonOpticalParentBirths;
    const auto found = fPendingBirths.find(child);
    G4int serial = 0;
    if (found == fPendingBirths.end()) {
      serial = ++fNextBirthSerial;
      fPendingBirths.emplace(child, PendingBirth{serial, parent->GetTrackID()});
    }
    else {
      serial = found->second.serial;
      ++fDuplicateBirths;
    }
    std::array<std::string, 24> row{};
    row[0] = "Birth";
    row[1] = std::to_string(fBirthAuditEventID);
    row[2] = std::to_string(serial);
    row[3] = AddressString(child);
    row[4] = std::to_string(child->GetTrackID());
    row[5] = std::to_string(parent->GetTrackID());
    row[6] = parent->GetDefinition()->GetParticleName();
    row[7] = std::to_string(parent->GetCurrentStepNumber());
    row[8] = creator->GetProcessName();
    row[9] = stepProcess ? stepProcess->GetProcessName() : "";
    row[10] = std::to_string(static_cast<G4int>(step->GetPostStepPoint()->GetStepStatus()));
    std::ostringstream energy;
    energy << std::setprecision(17) << step->GetTotalEnergyDeposit() / MeV;
    row[11] = energy.str();
    WriteBirthAuditRow(row);
  }
}

void W08PhotonDiagnostics::BindBirth(const G4Track* track)
{
  std::array<std::string, 24> row{};
  row[0] = "Bind";
  row[1] = std::to_string(fBirthAuditEventID);
  row[3] = AddressString(track);
  row[4] = std::to_string(track->GetTrackID());
  row[5] = std::to_string(track->GetParentID());
  const auto found = fPendingBirths.find(track);
  if (found == fPendingBirths.end()) {
    row[2] = "-1";
    ++fUnmatchedBirthBindings;
  }
  else {
    row[2] = std::to_string(found->second.serial);
    if (found->second.parentID != track->GetParentID()) ++fBirthParentMismatches;
    ++fLinkedStarts;
    fPendingBirths.erase(found);
  }
  WriteBirthAuditRow(row);
}

void W08PhotonDiagnostics::WriteBirthAuditSummary(G4int eventID, G4int generatedReference)
{
  std::array<std::string, 24> row{};
  row[0] = "EventSummary";
  row[1] = std::to_string(eventID);
  row[15] = std::to_string(fNonOpticalParentBirths);
  row[16] = std::to_string(fOpticalParentBirths);
  row[17] = std::to_string(fStarted);
  row[18] = std::to_string(generatedReference);
  row[19] = std::to_string(fPendingBirths.size());
  row[20] = std::to_string(fUnmatchedBirthBindings);
  row[21] = std::to_string(fDuplicateBirths);
  row[22] = std::to_string(fBirthParentMismatches);
  row[23] = std::to_string(fLinkedStarts);
  WriteBirthAuditRow(row);
  fBirthAudit.flush();
}

W08PhotonDiagnostics::Surface W08PhotonDiagnostics::ClassifyBox(
  const G4ThreeVector& point, const G4ThreeVector& center,
  const G4ThreeVector& halfSize, G4bool tile) const
{
  const auto q = point - center;
  Surface result;
  G4int matches = 0;
  const G4int minusRegions[] = {tile ? kTileXMinus : kSiPMXMinus,
                                tile ? kTileYMinus : kSiPMYMinus,
                                tile ? kTileBottom : kSiPMBack};
  const G4int plusRegions[] = {tile ? kTileXPlus : kSiPMXPlus,
                               tile ? kTileYPlus : kSiPMYPlus,
                               tile ? kTileTop : kSiPMFront};
  for (G4int axis = 0; axis < 3; ++axis) {
    if (std::abs(std::abs(q[axis]) - halfSize[axis]) > fRegionTolerance) continue;
    const G4int other1 = (axis + 1) % 3;
    const G4int other2 = (axis + 2) % 3;
    if (std::abs(q[other1]) > halfSize[other1] + fRegionTolerance ||
        std::abs(q[other2]) > halfSize[other2] + fRegionTolerance) continue;
    ++matches;
    result.region = q[axis] < 0. ? minusRegions[axis] : plusRegions[axis];
    result.outwardNormal = G4ThreeVector();
    result.outwardNormal[axis] = q[axis] < 0. ? -1. : 1.;
  }
  if (matches > 1) {
    result.region = kEdge;
    result.outwardNormal = G4ThreeVector();
  }
  return result;
}

W08PhotonDiagnostics::Surface W08PhotonDiagnostics::ClassifyTile(const G4ThreeVector& point) const
{
  auto box = ClassifyBox(point, fGeometry.tileCenter, fGeometry.tileHalfSize, true);
  if (!fGeometry.dimple) return box;
  const auto fromCenter = point - fGeometry.dimpleCenter;
  const G4double distance = fromCenter.mag();
  const G4bool onSphere =
    std::abs(distance - fGeometry.dimpleRadius) <= fRegionTolerance &&
    point.z() >= fGeometry.dimpleCenter.z() - fRegionTolerance;
  // The removed interior is not a surviving part of the tile's bottom plane.
  if (distance < fGeometry.dimpleRadius - fRegionTolerance) box = Surface{};
  if (!onSphere) return box;
  if (box.region != kUnknown) return Surface{kEdge, G4ThreeVector()};
  // The outward normal of a subtracted sphere points INTO the void.
  return Surface{kDimple, -fromCenter.unit()};
}

G4OpBoundaryProcess* W08PhotonDiagnostics::BoundaryProcess()
{
  if (fBoundaryProcessSearched) return fBoundaryProcess;
  fBoundaryProcessSearched = true;
  auto* manager = G4OpticalPhoton::OpticalPhotonDefinition()->GetProcessManager();
  auto* processes = manager->GetPostStepProcessVector(typeDoIt);
  for (G4int index = 0; index < processes->entries(); ++index) {
    if (auto* boundary = dynamic_cast<G4OpBoundaryProcess*>((*processes)[index])) {
      fBoundaryProcess = boundary;
      break;
    }
  }
  return fBoundaryProcess;
}

G4int W08PhotonDiagnostics::IncidenceBin(const G4ThreeVector& momentum,
                                        const G4ThreeVector& normal)
{
  if (momentum.mag2() == 0. || normal.mag2() == 0.) {
    ++fInvalidNormals;
    return -1;
  }
  G4double cosine = momentum.unit().dot(normal.unit());
  if (!std::isfinite(cosine) || cosine < -1.e-10 || cosine > 1. + 1.e-10) {
    ++fInvalidNormals;
    return -1;
  }
  cosine = std::max(0., std::min(1., cosine));
  const auto angle = std::acos(cosine);
  return std::min(17, static_cast<G4int>(angle / (5. * deg)));
}

void W08PhotonDiagnostics::ObserveBoundary(const G4Step* step, TrackRecord& record)
{
  const auto* pre = step->GetPreStepPoint();
  const auto* post = step->GetPostStepPoint();
  if (post->GetStepStatus() == fWorldBoundary && !post->GetPhysicalVolume()) {
    record.worldEscape = true;
    ++fBoundaryCounts[BoundaryKey{kWorldInterface, kWorld, 0, -1, -1}];
    return;
  }
  if (post->GetStepStatus() != fGeomBoundary) return;
  const auto* boundary = BoundaryProcess();
  record.boundaryStatus = boundary ? boundary->GetStatus() : Undefined;
  const G4bool technical = IsTechnical(record.boundaryStatus);
  if (technical) ++fBoundaryTechnical;
  if (!boundary) ++fUnknownRegions;

  const auto* prePV = pre->GetPhysicalVolume();
  const auto* postPV = post->GetPhysicalVolume();
  const G4bool preTile = prePV == fGeometry.tilePV;
  const G4bool postTile = postPV == fGeometry.tilePV;
  const G4bool preSiPM = prePV == fGeometry.sipmPV;
  const G4bool postSiPM = postPV == fGeometry.sipmPV;
  const G4bool preAir = prePV == fGeometry.worldPV;
  const G4bool postAir = postPV == fGeometry.worldPV;
  G4int interface = kOtherInterface;
  G4int direction = 0;
  Surface surface;
  G4ThreeVector incidentNormal;
  if ((preTile && postAir) || (preAir && postTile) ||
      (preTile && postSiPM) || (preSiPM && postTile)) {
    interface = preSiPM || postSiPM ? kTileSiPM : kTileAir;
    direction = preTile ? 1 : -1;
    surface = ClassifyTile(post->GetPosition());
    incidentNormal = direction * surface.outwardNormal;
  }
  else if ((preAir && postSiPM) || (preSiPM && postAir)) {
    interface = kAirSiPM;
    direction = postSiPM ? 1 : -1;
    surface = ClassifyBox(post->GetPosition(), fGeometry.sipmCenter,
                          fGeometry.sipmHalfSize, false);
    incidentNormal = -direction * surface.outwardNormal;
  }

  G4int angleBin = -1;
  if (!technical) {
    if (surface.region == kEdge) {
      ++fAmbiguousBoundaries;
      record.everAmbiguous = true;
    }
    else if (surface.region == kUnknown || interface == kOtherInterface) ++fUnknownRegions;
    else angleBin = IncidenceBin(pre->GetMomentumDirection(), incidentNormal);
    if (surface.region == kDimple) record.everDimple = true;
    if (preTile && (record.boundaryStatus == FresnelRefraction ||
                    record.boundaryStatus == Transmission)) {
      record.lastExitRegion = surface.region;
    }
  }
  ++fBoundaryCounts[BoundaryKey{interface, surface.region, direction,
                                record.boundaryStatus, angleBin}];
}

void W08PhotonDiagnostics::ObserveStep(const G4Step* step)
{
  if (fBirthAudit.is_open()) ObserveBirths(step);
  const auto* track = step->GetTrack();
  const auto* pre = step->GetPreStepPoint();
  const G4bool optical = IsOptical(track);
  if (pre->GetPhysicalVolume() == fGeometry.tilePV && step->GetTotalEnergyDeposit() > 0.) {
    if (optical) fOpticalEnergyDeposit += step->GetTotalEnergyDeposit();
    else fNonOpticalEnergyDeposit += step->GetTotalEnergyDeposit();
  }
  if (!optical) return;
  auto& record = fTracks[track->GetTrackID()];
  if (record.lastObservedStep == track->GetCurrentStepNumber()) return;
  record.lastObservedStep = track->GetCurrentStepNumber();
  record.preVolume = pre->GetPhysicalVolume();
  record.boundaryStatus = -1;
  record.worldEscape = false;
  const auto* process = step->GetPostStepPoint()->GetProcessDefinedStep();
  record.processName = process ? process->GetProcessName() : "";
  ObserveBoundary(step, record);
}

void W08PhotonDiagnostics::ObserveSiPMCollection(const G4Step* step)
{
  auto& record = fTracks[step->GetTrack()->GetTrackID()];
  record.collected = true;
  const auto surface = ClassifyBox(step->GetPreStepPoint()->GetPosition(),
                                   fGeometry.sipmCenter, fGeometry.sipmHalfSize, false);
  G4int face = 6;
  if (surface.region >= kSiPMFront && surface.region <= kSiPMYPlus) face = surface.region - kSiPMFront;
  else if (surface.region == kEdge) record.everAmbiguous = true;
  else ++fUnknownRegions;
  ++fSiPMFaces[static_cast<std::size_t>(face)];
}

void W08PhotonDiagnostics::EndTracking(const G4Track* track)
{
  if (!IsOptical(track)) return;
  if (track->GetTrackStatus() != fStopAndKill &&
      track->GetTrackStatus() != fKillTrackAndSecondaries) return;
  auto& record = fTracks[track->GetTrackID()];
  if (record.finalized) {
    ++fDuplicateFinalizations;
    return;
  }
  G4int fate = kOtherTermination;
  if (record.collected) fate = kCollected;
  else if (record.processName == "OpAbsorption") {
    fate = record.preVolume == fGeometry.tilePV ? kTileAbsorption :
           record.preVolume == fGeometry.worldPV ? kAirAbsorption : kOtherAbsorption;
  }
  else if (record.boundaryStatus == Absorption) fate = kBoundaryAbsorption;
  else if (record.boundaryStatus == Detection) fate = kBoundaryDetection;
  else if (record.worldEscape) fate = kWorldEscape;
  else if (record.processName == "OpWLS" || record.processName == "OpWLS2") fate = kWLSConversion;
  record.finalized = true;
  ++fFinalized;
  ++fFates[static_cast<std::size_t>(fate)];
  ++fTerminalCounts[TerminalKey{fate, record.lastExitRegion, record.everDimple ? 1 : 0}];
}

void W08PhotonDiagnostics::WriteEvent(G4int runID, G4int eventID, G4int generatedReference)
{
  if (fEventWritten) return;
  fEventWritten = true;
  G4int unresolved = 0;
  G4int ambiguousPhotons = 0;
  for (const auto& entry : fTracks) {
    if (!entry.second.finalized) ++unresolved;
    if (entry.second.everAmbiguous) ++ambiguousPhotons;
  }
  auto analysis = G4AnalysisManager::Instance();
  const std::array<G4int, 28> values = {{
    runID, eventID, generatedReference, fStarted, fFinalized,
    fFates[kCollected], fFates[kTileAbsorption], fFates[kAirAbsorption], fFates[kOtherAbsorption],
    fFates[kBoundaryAbsorption], fFates[kBoundaryDetection], fFates[kWorldEscape],
    fFates[kWLSConversion], fFates[kOtherTermination], unresolved, fDuplicateFinalizations,
    fUnknownRegions, fAmbiguousBoundaries, fInvalidNormals, fBoundaryTechnical, fUnsupportedBirths,
    fSiPMFaces[0], fSiPMFaces[1], fSiPMFaces[2], fSiPMFaces[3], fSiPMFaces[4], fSiPMFaces[5], fSiPMFaces[6]
  }};
  for (G4int column = 0; column < static_cast<G4int>(values.size()); ++column) {
    analysis->FillNtupleIColumn(eventNtuple, column, values[static_cast<std::size_t>(column)]);
  }
  analysis->FillNtupleDColumn(eventNtuple, 28, fNonOpticalEnergyDeposit / MeV);
  analysis->FillNtupleDColumn(eventNtuple, 29, fOpticalEnergyDeposit / MeV);
  analysis->FillNtupleIColumn(eventNtuple, 30, ambiguousPhotons);
  analysis->AddNtupleRow(eventNtuple);

  for (const auto& entry : fTerminalCounts) {
    analysis->FillNtupleIColumn(terminalNtuple, 0, runID);
    analysis->FillNtupleIColumn(terminalNtuple, 1, eventID);
    for (G4int column = 0; column < 3; ++column) {
      analysis->FillNtupleIColumn(terminalNtuple, column + 2, entry.first[static_cast<std::size_t>(column)]);
    }
    analysis->FillNtupleIColumn(terminalNtuple, 5, entry.second);
    analysis->AddNtupleRow(terminalNtuple);
  }
  for (const auto& entry : fBoundaryCounts) {
    analysis->FillNtupleIColumn(boundaryNtuple, 0, runID);
    analysis->FillNtupleIColumn(boundaryNtuple, 1, eventID);
    for (G4int column = 0; column < 5; ++column) {
      analysis->FillNtupleIColumn(boundaryNtuple, column + 2, entry.first[static_cast<std::size_t>(column)]);
    }
    analysis->FillNtupleIColumn(boundaryNtuple, 7, entry.second);
    analysis->AddNtupleRow(boundaryNtuple);
  }
  if (fBirthAudit.is_open()) WriteBirthAuditSummary(eventID, generatedReference);
}
