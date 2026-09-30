//
// ********************************************************************
// * License and Disclaimer                                           *
// *                                                                  *
// * The  Geant4 software  is  copyright of the Copyright Holders  of *
// * the Geant4 Collaboration.  It is provided  under  the terms  and *
// * conditions of the Geant4 Software License,  included in the file *
// * LICENSE and available at  http://cern.ch/geant4/license .  These *
// * include a list of copyright holders.                             *
// *                                                                  *
// * Neither the authors of this software system, nor their employing *
// * institutes,nor the agencies providing financial support for this *
// * work  make  any representation or  warranty, express or implied, *
// * regarding  this  software system or assume any liability for its *
// * use.  Please see the license in the file  LICENSE  and URL above *
// * for the full disclaimer and the limitation of liability.         *
// *                                                                  *
// * This  code  implementation is the result of  the  scientific and *
// * technical work of the GEANT4 collaboration.                      *
// * By using,  copying,  modifying or  distributing the software (or *
// * any work based  on the software)  you  agree  to acknowledge its *
// * use  in  resulting  scientific  publications,  and indicate your *
// * acceptance of all terms of the Geant4 Software license.          *
// ********************************************************************
//
/// \file optical/OpNovice2/src/RunAction.cc
/// \brief Implementation of the RunAction class
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "RunAction.hh"
#include "OpticalNumerics.hh"

#include "HistoManager.hh"
#include "DetectorConstruction.hh"
#include "PrimaryGeneratorAction.hh"
#include "Run.hh"
#include "SteppingAction.hh"
#include "W08PhotonDiagnostics.hh"
#include "StackPhotonAccounting.hh"

#include "G4Exception.hh"
#include "G4GeneralParticleSource.hh"
#include "G4OpticalParameters.hh"
#include "G4OpticalPhoton.hh"
#include "G4ParticleTable.hh"
#include "G4ProcessManager.hh"
#include "G4Run.hh"
#include "G4RunManager.hh"
#include "G4SingleParticleSource.hh"
#include "G4SPSAngDistribution.hh"
#include "G4SPSEneDistribution.hh"
#include "G4SPSPosDistribution.hh"
#include "G4SystemOfUnits.hh"
#include "G4UnitsTable.hh"
#include "G4VProcess.hh"
#include "G4VPhysicalVolume.hh"
#include "G4Version.hh"
#include "Randomize.hh"

#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
namespace
{
  G4bool ProcessIsActive(G4ParticleDefinition* particle, const G4String& name)
  {
    if (!particle || !particle->GetProcessManager()) return false;
    const auto* manager = particle->GetProcessManager();
    const auto* processes = manager->GetProcessList();
    for (G4int index = 0; index < manager->GetProcessListLength(); ++index) {
      auto* process = (*processes)[index];
      if (process && process->GetProcessName() == name && manager->GetProcessActivation(process)) return true;
    }
    return false;
  }

  void ValidateW08SourceAndPhysics(PrimaryGeneratorAction* primary)
  {
    if (!primary || G4RunManager::GetRunManager()->GetRunManagerType() != G4RunManager::sequentialRM) {
      G4ExceptionDescription message;
      message << "W08 diagnostics requires the Serial run manager and its primary generator. "
              << "Set G4RUN_MANAGER_TYPE=Serial; the RNG end-state gate does not cover MT workers.";
      G4Exception("RunAction::ValidateW08SourceAndPhysics", "W08_Serial_001", FatalException, message);
      return;
    }
    auto* gps = primary->GetGeneralParticleSource();
    auto* source = gps ? gps->GetCurrentSource() : nullptr;
    auto* particle = source ? source->GetParticleDefinition() : nullptr;
    G4ExceptionDescription errors;
    G4bool valid = true;
    const auto require = [&errors, &valid](G4bool condition, const char* message) {
      if (!condition) { valid = false; errors << message << '\n'; }
    };
    require(gps && source, "W08 requires a configured GPS source.");
    if (gps && source) {
      require(gps->GetNumberofSource() == 1 && source->GetNumberOfParticles() == 1,
              "W08 requires exactly one GPS source and one primary per event.");
      require(particle && particle->GetParticleName() == "e-", "W08 requires electron primaries.");
      require(primary->GetElectronEnergyMode() == "fixed", "W08 electronEnergyMode must remain fixed.");
      auto* energy = source->GetEneDist();
      auto* position = source->GetPosDist();
      auto* angle = source->GetAngDist();
      require(energy->GetEnergyDisType() == "Mono" &&
                std::abs(energy->GetMonoEnergy() - 1. * MeV) <= 1.e-12 * MeV,
              "W08 requires 1 MeV Mono energy, without an energy spectrum.");
      const auto center = position->GetCentreCoords();
      require(position->GetPosDisType() == "Point" &&
                std::abs(center.z() - 4. * mm) <= 1.e-9 * mm &&
                std::abs(center.x()) <= 50. * mm && std::abs(center.y()) <= 50. * mm,
              "W08 requires a Point source at z=4 mm with x/y within the tile footprint.");
      require(angle->GetDistType() == "planar" &&
                (angle->GetDirection() - G4ThreeVector(0., 0., -1.)).mag2() <= 1.e-24,
              "W08 requires fixed planar direction (0,0,-1), without divergence.");
    }
    const auto* optical = G4OpticalParameters::Instance();
    require(!optical->GetProcessActivation("Cerenkov") && !ProcessIsActive(particle, "Cerenkov"),
            "W08 requires Cerenkov to be inactive.");
    require(optical->GetProcessActivation("Scintillation") && ProcessIsActive(particle, "Scintillation"),
            "W08 requires Scintillation to be active.");
    auto* photon = G4OpticalPhoton::OpticalPhotonDefinition();
    // The corrected W08 physics baseline disables this process only for
    // optical photons. The observer never changes activation itself.
    require(!ProcessIsActive(photon, "Scintillation"),
            "Corrected W08 requires /process/inactivate Scintillation opticalphoton after /run/initialize; keep electron Scintillation active.");
    require(optical->GetProcessActivation("OpAbsorption") && ProcessIsActive(photon, "OpAbsorption"),
            "W08 requires OpAbsorption to be active.");
    require(optical->GetProcessActivation("OpBoundary") && ProcessIsActive(photon, "OpBoundary"),
            "W08 requires OpBoundary to be active.");
    for (const auto* name : {"OpRayleigh", "OpMieHG", "OpWLS"}) {
      if (optical->GetProcessActivation(name) || ProcessIsActive(photon, name)) {
        valid = false;
        errors << "W08 requires " << name << " to remain inactive.\n";
      }
    }
    // WLS2 activation is intentionally left at the original baseline value;
    // any actual conversion is reported by the terminal/event audit.
    require(optical->GetScintStackPhotons(), "W08 requires scintillation photons to be stacked.");
    const auto* stepping = dynamic_cast<const SteppingAction*>(
      G4RunManager::GetRunManager()->GetUserSteppingAction());
    require(stepping && !stepping->GetKillOnSecondSurface(),
            "W08 requires killOnSecondSurface=false.");
    if (!valid) {
      G4Exception("RunAction::ValidateW08SourceAndPhysics", "W08_SourcePhysics_001", FatalException, errors);
    }
  }

  void WriteRandomEngineEndState(const G4String& outputBaseName)
  {
    G4String stem = outputBaseName;
    if (stem.size() >= 5 && stem.substr(stem.size() - 5) == ".root") {
      stem.erase(stem.size() - 5);
    }
    std::ofstream output(stem + ".rng-end.txt");
    if (!output) {
      G4cerr << "Could not write random engine end state: " << stem << ".rng-end.txt" << G4endl;
      return;
    }
    // CLHEP put() serializes the current engine; it does not draw a number.
    G4Random::getTheEngine()->put(output);
  }

  void WritePhysicsEndState(const G4String& outputBaseName)
  {
    G4String stem = outputBaseName;
    if (stem.size() >= 5 && stem.substr(stem.size() - 5) == ".root") {
      stem.erase(stem.size() - 5);
    }
    std::ofstream output(stem + ".physics-state.json");
    if (!output) {
      G4cerr << "Could not write physics end state: " << stem << ".physics-state.json" << G4endl;
      return;
    }
    // Query existing particle definitions and their actual process-manager
    // activation, independently of the diagnostic switch or global defaults.
    auto* particles = G4ParticleTable::GetParticleTable();
    auto* electron = particles->FindParticle("e-");
    auto* photon = particles->FindParticle("opticalphoton");
    const auto* optical = G4OpticalParameters::Instance();
    const auto* stepping = dynamic_cast<const SteppingAction*>(
      G4RunManager::GetRunManager()->GetUserSteppingAction());
    output << std::boolalpha
           << "{\n  \"electron_scintillation_active\": " << ProcessIsActive(electron, "Scintillation")
           << ",\n  \"optical_scintillation_active\": " << ProcessIsActive(photon, "Scintillation")
           << ",\n  \"optical_wls2_active\": " << ProcessIsActive(photon, "OpWLS2")
           << ",\n  \"electron_cerenkov_active\": " << ProcessIsActive(electron, "Cerenkov")
           << ",\n  \"optical_absorption_active\": " << ProcessIsActive(photon, "OpAbsorption")
           << ",\n  \"optical_boundary_active\": " << ProcessIsActive(photon, "OpBoundary")
           << ",\n  \"optical_rayleigh_active\": " << ProcessIsActive(photon, "OpRayleigh")
           << ",\n  \"optical_miehg_active\": " << ProcessIsActive(photon, "OpMieHG")
           << ",\n  \"optical_wls_active\": " << ProcessIsActive(photon, "OpWLS")
           << ",\n  \"scintillation_by_particle_type\": " << optical->GetScintByParticleType()
           << ",\n  \"scintillation_track_info\": " << optical->GetScintTrackInfo()
           << ",\n  \"scintillation_finite_rise_time\": " << optical->GetScintFiniteRiseTime()
           << ",\n  \"scintillation_stack_photons\": " << optical->GetScintStackPhotons()
           << ",\n  \"scintillation_track_secondaries_first\": " << optical->GetScintTrackSecondariesFirst()
           << ",\n  \"kill_on_second_surface\": " << (stepping && stepping->GetKillOnSecondSurface())
           << ",\n  \"geant4_version\": \"" << G4VERSION_NUMBER / 100 << '.'
           << (G4VERSION_NUMBER / 10) % 10 << '.' << G4VERSION_NUMBER % 10 << "\"\n}\n";
  }

  void JsonVector(std::ostream& output, const G4ThreeVector& vector, G4double unit = mm)
  {
    output << '[' << vector.x() / unit << ',' << vector.y() / unit << ',' << vector.z() / unit << ']';
  }

  void WriteStackRuntime(const DetectorConstruction& detector,
                         PrimaryGeneratorAction* primary, const G4String& outputBaseName)
  {
    if (!detector.IsStackV2() || !primary) return;
    G4String stem = outputBaseName;
    if (stem.size() >= 5 && stem.substr(stem.size() - 5) == ".root") stem.erase(stem.size() - 5);
    std::ofstream output(stem + ".stack-v2-runtime.json");
    if (!output) {
      G4cerr << "Could not write stack runtime state: " << stem << ".stack-v2-runtime.json" << G4endl;
      return;
    }
    output << std::setprecision(17) << std::boolalpha
           << "{\n  \"schema_version\": \"steel-stack-v2-runtime-v1\",\n  \"model\": \"v2\","
           << "\n  \"run_manager\": \"Serial\","
           << "\n  \"layout\": \"" << (detector.GetSiPMLayout() == "single" ? "back-center" : detector.GetSiPMLayout()) << "\","
           << "\n  \"layers\": " << detector.GetStackLayerCount()
           << ",\n  \"readout_gap_mm\": " << detector.GetStackReadoutGap() / mm
           << ",\n  \"core_length_mm\": " << detector.GetStackCoreLength() / mm
           << ",\n  \"copy_stride\": " << detector.GetSensorCopyStride()
           << ",\n  \"active_sensors_per_layer\": " << detector.GetActiveSensorsPerLayer()
           << ",\n  \"accounting_enabled\": " << detector.IsStackAccountingEnabled()
           << ",\n  \"tile_full_size_mm\": ";
    JsonVector(output, 2. * detector.GetTileHalfSize());
    output << ",\n  \"optical_numerics\": ";
    OpticalNumerics::Instance().WriteIdentity(output);
    output << ",\n  \"steel_full_size_mm\": "; JsonVector(output, detector.GetAbsorberFullSize());
    output << ",\n  \"world_full_size_mm\": "; JsonVector(output, 2. * detector.GetWorldHalfSize());
    output << ",\n  \"tile_centers_mm\": [";
    G4bool first = true;
    for (const auto* volume : detector.GetStackTiles()) {
      if (!first) output << ',';
      first = false; JsonVector(output, volume->GetObjectTranslation());
    }
    output << "],\n  \"steel_centers_mm\": [";
    first = true;
    for (const auto* volume : detector.GetStackAbsorbers()) {
      if (!first) output << ',';
      first = false; JsonVector(output, volume->GetObjectTranslation());
    }
    output << "],\n  \"sensor_map\": [";
    first = true;
    for (const auto* sensor : detector.GetSiPMs()) {
      if (!first) output << ',';
      first = false;
      const auto copy = sensor->GetCopyNo();
      output << "\n    {\"global_copy\":" << copy
             << ",\"layer\":" << detector.GetSensorLayer(copy)
             << ",\"local_sensor\":" << detector.GetSensorLocalIndex(copy)
             << ",\"center_mm\":";
      JsonVector(output, sensor->GetObjectTranslation());
      output << ",\"half_size_mm\":"; JsonVector(output, detector.GetSiPMHalfSize());
      output << ",\"face\":\"" << detector.GetSiPMFace() << "\"}";
    }
    if (OpticalNumerics::Instance().IsProbe()) {
      output << "\n  ],\n  \"source\": {\"particle\":\"opticalphoton\",\"profile\":\"registered-deterministic-optical-table\",\"primaries_per_event\":1},\n";
    } else {
    auto* gps = primary->GetGeneralParticleSource();
    auto* source = gps->GetCurrentSource();
    output << "\n  ],\n  \"source\": {\"particle\":\"" << source->GetParticleDefinition()->GetParticleName()
           << "\",\"energy_mev\":" << source->GetEneDist()->GetMonoEnergy() / MeV << ",\"position_mm\":";
    JsonVector(output, source->GetPosDist()->GetCentreCoords());
    output << ",\"direction\":"; JsonVector(output, source->GetAngDist()->GetDirection(), 1.);
    output << ",\"position_distribution\":\"" << source->GetPosDist()->GetPosDisType()
           << "\",\"energy_distribution\":\"" << source->GetEneDist()->GetEnergyDisType()
           << "\",\"angular_distribution\":\"" << source->GetAngDist()->GetDistType()
           << "\",\"number_of_sources\":" << gps->GetNumberofSource()
           << ",\"primaries_per_event\":" << source->GetNumberOfParticles() << "},\n";
    }
    auto* particles = G4ParticleTable::GetParticleTable();
    auto* electron = particles->FindParticle("e-");
    auto* photon = particles->FindParticle("opticalphoton");
    const auto* optical = G4OpticalParameters::Instance();
    const auto* stepping = dynamic_cast<const SteppingAction*>(
      G4RunManager::GetRunManager()->GetUserSteppingAction());
    output << "  \"physics\": {\"requested_policy\":\"legacy-steel-optical-macro\","
           << "\"actual_process_activation\":{"
           << "\"Cerenkov\":" << ProcessIsActive(electron, "Cerenkov")
           << ",\"Scintillation\":" << ProcessIsActive(electron, "Scintillation")
           << ",\"OpAbsorption\":" << ProcessIsActive(photon, "OpAbsorption")
           << ",\"OpBoundary\":" << ProcessIsActive(photon, "OpBoundary")
           << ",\"OpRayleigh\":" << ProcessIsActive(photon, "OpRayleigh")
           << ",\"OpMieHG\":" << ProcessIsActive(photon, "OpMieHG")
           << ",\"OpWLS\":" << ProcessIsActive(photon, "OpWLS")
           << ",\"OpWLS2\":" << ProcessIsActive(photon, "OpWLS2") << "},"
           << "\"opticalphoton_scintillation_active\":" << ProcessIsActive(photon, "Scintillation")
           << ",\"scintillation_by_particle_type\":" << optical->GetScintByParticleType()
           << ",\"scintillation_track_info\":" << optical->GetScintTrackInfo()
           << ",\"scintillation_finite_rise_time\":" << optical->GetScintFiniteRiseTime()
           << ",\"scintillation_stack_photons\":" << optical->GetScintStackPhotons()
           << ",\"scintillation_track_secondaries_first\":" << optical->GetScintTrackSecondariesFirst()
           << ",\"kill_on_second_surface\":" << (stepping && stepping->GetKillOnSecondSurface()) << "}\n}\n";
  }

  void WriteScanSummary(const Run* run, const G4String& outputBaseName)
  {
    G4String summaryBaseName = outputBaseName;
    const auto rootExtension = summaryBaseName.rfind(".root");
    if (rootExtension != G4String::npos
        && rootExtension == summaryBaseName.size() - G4String(".root").size())
    {
      summaryBaseName.erase(rootExtension);
    }
    const G4String summaryFileName = summaryBaseName + "_summary.csv";
    std::ofstream out(summaryFileName);
    if (!out) {
      G4cerr << "Could not write scan summary CSV: " << summaryFileName << G4endl;
      return;
    }

    const auto generatedOptical = run->GetGeneratedOpticalCount();
    const auto sipmDetected = run->GetSiPMDetectionCount();
    const auto committedEvents = run->GetCommittedEventCount();
    const auto collectionEfficiencyValid = generatedOptical > 0;
    const auto collectionEfficiency =
      collectionEfficiencyValid
        ? G4double(sipmDetected) / G4double(generatedOptical)
        : std::numeric_limits<G4double>::quiet_NaN();
    const auto production = committedEvents > 0
      ? G4double(run->GetScintillationCount()) / G4double(committedEvents)
      : std::numeric_limits<G4double>::quiet_NaN();
    const auto netResponse = committedEvents > 0
      ? G4double(sipmDetected) / G4double(committedEvents)
      : std::numeric_limits<G4double>::quiet_NaN();
    const auto zeroFraction = [committedEvents](G4long nonzeroEvents) {
      return committedEvents > 0
        ? G4double(committedEvents - nonzeroEvents) / G4double(committedEvents)
        : std::numeric_limits<G4double>::quiet_NaN();
    };
    const auto missingPosition = std::numeric_limits<G4double>::quiet_NaN();
    const auto shootPosition = run->GetMeanShootPosition();
    const auto hitPosition = run->GetMeanHitPosition();
    const auto scintCentroid = run->GetMeanScintillationCentroid();
    const auto primaryEnergyCount = run->GetPrimaryKineticEnergyCount();
    const auto decayBetaCount = run->GetDecayBetaCount();

    out << "events,generated_optical_photons,scintillation_photons,"
        << "sipm_detected_photons,collection_efficiency,"
        << "shoot_position_events,shoot_x_mm,shoot_y_mm,shoot_z_mm,"
        << "hit_position_events,hit_x_mm,hit_y_mm,hit_z_mm,"
        << "scint_centroid_events,scint_centroid_x_mm,scint_centroid_y_mm,"
        << "scint_centroid_z_mm,"
        << "primary_energy_events,primary_energy_mean_mev,primary_energy_rms_mev,"
        << "primary_energy_min_mev,primary_energy_max_mev,"
        << "decay_beta_count,decay_beta_energy_mean_mev,decay_beta_energy_rms_mev,"
        << "decay_beta_energy_min_mev,decay_beta_energy_max_mev,"
        << "committed_events,collection_efficiency_valid,"
        << "production_scint_photons_per_event,net_sipm_photons_per_event,"
        << "cerenkov_photons,steel_edep_sum_mev,steel_edep_mean_mev,"
        << "steel_edep_rms_mev,steel_edep_se_mev,steel_edep_nonzero_events,"
        << "primary_neutron_interaction_events,primary_neutron_elastic_count,"
        << "primary_neutron_inelastic_count,primary_neutron_capture_count,"
        << "primary_neutron_elastic_events,primary_neutron_inelastic_events,"
        << "primary_neutron_capture_events,charged_tile_entry_events,"
        << "charged_tile_entry_count,charged_tile_entry_ke_sum_mev,"
        << "electron_tile_entry_count,electron_tile_entry_ke_sum_mev,"
        << "proton_tile_entry_count,proton_tile_entry_ke_sum_mev,"
        << "other_charged_tile_entry_count,other_charged_tile_entry_ke_sum_mev,"
        << "primary_neutron_tile_entry_events,tile_edep_sum_mev,"
        << "electron_tile_edep_sum_mev,proton_tile_edep_sum_mev,"
        << "other_charged_tile_edep_sum_mev,neutral_tile_edep_sum_mev,"
        << "tile_edep_mean_mev,tile_edep_rms_mev,tile_edep_se_mev,"
        << "tile_edep_nonzero_events,generated_optical_mean,generated_optical_rms,"
        << "generated_optical_se,generated_optical_zero_events,"
        << "generated_optical_zero_fraction,"
        << "scintillation_mean,scintillation_rms,scintillation_se,"
        << "scintillation_zero_events,scintillation_zero_fraction,"
        << "sipm_detected_mean,sipm_detected_rms,sipm_detected_se,"
        << "sipm_detected_zero_events,sipm_detected_zero_fraction\n";
    out << std::setprecision(17);
    out << run->GetNumberOfEvents() << ','
        << generatedOptical << ','
        << run->GetScintillationCount() << ','
        << sipmDetected << ','
        << collectionEfficiency << ','
        << run->GetShootPositionCount() << ','
        << (run->GetShootPositionCount() > 0 ? shootPosition.x() / mm : missingPosition) << ','
        << (run->GetShootPositionCount() > 0 ? shootPosition.y() / mm : missingPosition) << ','
        << (run->GetShootPositionCount() > 0 ? shootPosition.z() / mm : missingPosition) << ','
        << run->GetHitPositionCount() << ','
        << (run->GetHitPositionCount() > 0 ? hitPosition.x() / mm : missingPosition) << ','
        << (run->GetHitPositionCount() > 0 ? hitPosition.y() / mm : missingPosition) << ','
        << (run->GetHitPositionCount() > 0 ? hitPosition.z() / mm : missingPosition) << ','
        << run->GetScintillationCentroidCount() << ','
        << (run->GetScintillationCentroidCount() > 0 ? scintCentroid.x() / mm : missingPosition)
        << ','
        << (run->GetScintillationCentroidCount() > 0 ? scintCentroid.y() / mm : missingPosition)
        << ','
        << (run->GetScintillationCentroidCount() > 0 ? scintCentroid.z() / mm : missingPosition)
        << ','
        << primaryEnergyCount << ','
        << (primaryEnergyCount > 0 ? run->GetPrimaryKineticEnergyMean() / MeV : missingPosition)
        << ','
        << (primaryEnergyCount > 0 ? run->GetPrimaryKineticEnergyRms() / MeV : missingPosition)
        << ','
        << (primaryEnergyCount > 0 ? run->GetPrimaryKineticEnergyMin() / MeV : missingPosition)
        << ','
        << (primaryEnergyCount > 0 ? run->GetPrimaryKineticEnergyMax() / MeV : missingPosition)
        << ','
        << decayBetaCount << ','
        << (decayBetaCount > 0 ? run->GetDecayBetaEnergyMean() / MeV : missingPosition)
        << ','
        << (decayBetaCount > 0 ? run->GetDecayBetaEnergyRms() / MeV : missingPosition)
        << ','
        << (decayBetaCount > 0 ? run->GetDecayBetaEnergyMin() / MeV : missingPosition)
        << ','
        << (decayBetaCount > 0 ? run->GetDecayBetaEnergyMax() / MeV : missingPosition)
        << ','
        << committedEvents << ','
        << (collectionEfficiencyValid ? 1 : 0) << ','
        << production << ','
        << netResponse << ','
        << run->GetCerenkovCount() << ','
        << run->GetSteelEnergyDepositSum() / MeV << ','
        << run->GetSteelEnergyDepositMean() / MeV << ','
        << run->GetSteelEnergyDepositRms() / MeV << ','
        << run->GetSteelEnergyDepositStandardError() / MeV << ','
        << run->GetSteelEnergyDepositNonzeroEventCount() << ','
        << run->GetPrimaryNeutronInteractionEventCount() << ','
        << run->GetPrimaryNeutronElasticInteractionCount() << ','
        << run->GetPrimaryNeutronInelasticInteractionCount() << ','
        << run->GetPrimaryNeutronCaptureInteractionCount() << ','
        << run->GetPrimaryNeutronElasticEventCount() << ','
        << run->GetPrimaryNeutronInelasticEventCount() << ','
        << run->GetPrimaryNeutronCaptureEventCount() << ','
        << run->GetChargedTileEntryEventCount() << ','
        << run->GetChargedTileEntryCount() << ','
        << run->GetChargedTileEntryKineticEnergySum() / MeV << ','
        << run->GetElectronTileEntryCount() << ','
        << run->GetElectronTileEntryKineticEnergySum() / MeV << ','
        << run->GetProtonTileEntryCount() << ','
        << run->GetProtonTileEntryKineticEnergySum() / MeV << ','
        << run->GetOtherChargedTileEntryCount() << ','
        << run->GetOtherChargedTileEntryKineticEnergySum() / MeV << ','
        << run->GetPrimaryNeutronTileEntryEventCount() << ','
        << run->GetTileEnergyDepositSum() / MeV << ','
        << run->GetElectronTileEnergyDepositSum() / MeV << ','
        << run->GetProtonTileEnergyDepositSum() / MeV << ','
        << run->GetOtherChargedTileEnergyDepositSum() / MeV << ','
        << run->GetNeutralTileEnergyDepositSum() / MeV << ','
        << run->GetTileEnergyDepositMean() / MeV << ','
        << run->GetTileEnergyDepositRms() / MeV << ','
        << run->GetTileEnergyDepositStandardError() / MeV << ','
        << run->GetTileEnergyDepositNonzeroEventCount() << ','
        << run->GetGeneratedOpticalMean() << ','
        << run->GetGeneratedOpticalRms() << ','
        << run->GetGeneratedOpticalStandardError() << ','
        << committedEvents - run->GetGeneratedOpticalNonzeroEventCount() << ','
        << zeroFraction(run->GetGeneratedOpticalNonzeroEventCount()) << ','
        << run->GetScintillationMean() << ','
        << run->GetScintillationRms() << ','
        << run->GetScintillationStandardError() << ','
        << committedEvents - run->GetScintillationNonzeroEventCount() << ','
        << zeroFraction(run->GetScintillationNonzeroEventCount()) << ','
        << run->GetSiPMDetectionMean() << ','
        << run->GetSiPMDetectionRms() << ','
        << run->GetSiPMDetectionStandardError() << ','
        << committedEvents - run->GetSiPMDetectionNonzeroEventCount() << ','
        << zeroFraction(run->GetSiPMDetectionNonzeroEventCount())
        << '\n';
  }
}

RunAction::RunAction(PrimaryGeneratorAction* prim)
  : G4UserRunAction(), fRun(nullptr), fHistoManager(nullptr), fPrimary(prim)
{
  fHistoManager = new HistoManager();
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

RunAction::~RunAction()
{
  delete fHistoManager;
}

G4Run* RunAction::GenerateRun()
{
  fRun = new Run();
  return fRun;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

void RunAction::BeginOfRunAction(const G4Run*)
{
  const auto* detector = static_cast<const DetectorConstruction*>(
    G4RunManager::GetRunManager()->GetUserDetectorConstruction());
  if (detector && detector->IsStackV2() &&
      (!fPrimary || G4RunManager::GetRunManager()->GetRunManagerType() != G4RunManager::sequentialRM)) {
    G4ExceptionDescription message;
    message << "stack-v2 requires G4RUN_MANAGER_TYPE=Serial in both accounting modes. "
            << "A shared runtime sidecar and master-only RNG end state cannot certify MT workers.";
    G4Exception("RunAction::BeginOfRunAction", "StackV2_Serial_001", FatalException, message);
    return;
  }
  if (detector && detector->IsW08PhotonDiagnosticsEnabled()) {
    detector->ValidateW08PhotonLossConfiguration();
    ValidateW08SourceAndPhysics(fPrimary);
  }
  if (fPrimary) {
    G4ParticleDefinition* particle = fPrimary->GetGeneralParticleSource()->GetParticleDefinition();
    G4double energy = fPrimary->GetGeneralParticleSource()->GetParticleEnergy();
    G4bool polarized = fPrimary->GetPolarized();
    G4double polarization = fPrimary->GetPolarization();
    fRun->SetPrimary(particle, energy, polarized, polarization, fPrimary->GetElectronEnergyMode());
    if (fRun->GetW08Diagnostics()) {
      fRun->GetW08Diagnostics()->SetSourceIsElectron(particle && particle->GetParticleName() == "e-");
    }
  }

  // histograms
  G4AnalysisManager* analysisManager = G4AnalysisManager::Instance();
  if (detector && detector->IsW08PhotonDiagnosticsEnabled()) {
    W08PhotonDiagnostics::BookNtuples();
  }
  if (fRun->GetStackAccounting()) StackPhotonAccounting::BookNtuples();
  OpticalNumerics::Instance().BeginRun(analysisManager->GetFileName());
  if (detector) WriteStackRuntime(*detector, fPrimary, analysisManager->GetFileName());
  if (analysisManager->IsActive()) {
    analysisManager->OpenFile();
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

void RunAction::EndOfRunAction(const G4Run*)
{
  OpticalNumerics::Instance().EndRun();
  G4AnalysisManager* analysisManager = G4AnalysisManager::Instance();

  if (isMaster) {
    fRun->EndOfRun();
    WriteScanSummary(fRun, analysisManager->GetFileName());
  }

  G4cout << G4endl << " Histogram statistics for the ";
  if (isMaster) {
    G4cout << "entire run:" << G4endl << G4endl;
  }
  else {
    G4cout << "local thread:" << G4endl << G4endl;
  }

  G4int id = analysisManager->GetH1Id("Cerenkov spectrum");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Cerenkov spectrum: mean = " << analysisManager->GetH1(id)->mean()
           << " eV; rms = " << analysisManager->GetH1(id)->rms() << " eV." << G4endl;
  }
  id = analysisManager->GetH1Id("Scintillation spectrum");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Scintillation spectrum: mean = " << analysisManager->GetH1(id)->mean()
           << " eV; rms = " << analysisManager->GetH1(id)->rms() << " eV." << G4endl;
  }
  id = analysisManager->GetH1Id("Scintillation time");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Scintillation time: mean = " << analysisManager->GetH1(id)->mean()
           << " ns; rms = " << analysisManager->GetH1(id)->rms() << " ns." << G4endl;
  }
  id = analysisManager->GetH1Id("WLS abs");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " WLS absorption spectrum: mean = " << analysisManager->GetH1(id)->mean()
           << " eV; rms = " << analysisManager->GetH1(id)->rms() << " eV." << G4endl;
  }
  id = analysisManager->GetH1Id("WLS em");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " WLS emission spectrum: mean = " << analysisManager->GetH1(id)->mean()
           << " eV; rms = " << analysisManager->GetH1(id)->rms() << " eV." << G4endl;
  }
  id = analysisManager->GetH1Id("WLS time");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " WLS emission time: mean = " << analysisManager->GetH1(id)->mean()
           << " ns; rms = " << analysisManager->GetH1(id)->rms() << " ns." << G4endl;
  }
  id = analysisManager->GetH1Id("WLS2 abs");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " WLS emission time: mean = " << analysisManager->GetH1(id)->mean()
           << " ns; rms = " << analysisManager->GetH1(id)->rms() << " ns." << G4endl;
  }
  id = analysisManager->GetH1Id("WLS2 em");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " WLS2 emission spectrum: mean = " << analysisManager->GetH1(id)->mean()
           << " eV; rms = " << analysisManager->GetH1(id)->rms() << " eV." << G4endl;
  }
  id = analysisManager->GetH1Id("WLS2 time");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " WLS2 emission time: mean = " << analysisManager->GetH1(id)->mean()
           << " ns; rms = " << analysisManager->GetH1(id)->rms() << " ns." << G4endl;
  }
  id = analysisManager->GetH1Id("x_backward");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " X momentum dir of backward-going photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("y_backward");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Y momentum dir of backward-going photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("z_backward");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Z momentum dir of backward-going photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("x_forward");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " X momentum dir of forward-going photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("y_forward");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Y momentum dir of forward-going photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("z_forward");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Z momentum dir of forward-going photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("x_fresnel");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " X momentum dir of Fresnel-refracted photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("y_fresnel");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Y momentum dir of Fresnel-refracted photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("z_fresnel");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Z momentum dir of Fresnel-refracted photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }
  id = analysisManager->GetH1Id("Transmitted");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Angle of transmitted photons: mean = " << analysisManager->GetH1(id)->mean()
           << "; rms = " << analysisManager->GetH1(id)->rms() << G4endl;
  }
  id = analysisManager->GetH1Id("Fresnel reflection");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Angle of Fresnel-reflected photons: mean = " << analysisManager->GetH1(id)->mean()
           << "; rms = " << analysisManager->GetH1(id)->rms() << G4endl;
  }
  id = analysisManager->GetH1Id("Total internal reflection");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Angle of total internal reflected photons: mean = "
           << analysisManager->GetH1(id)->mean() << "; rms = " << analysisManager->GetH1(id)->rms()
           << G4endl;
  }

  id = analysisManager->GetH1Id("Fresnel refraction");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Angle of Fresnel-refracted photons: mean = " << analysisManager->GetH1(id)->mean()
           << "; rms = " << analysisManager->GetH1(id)->rms() << G4endl;
  }

  id = analysisManager->GetH1Id("Absorption");
  if (analysisManager->GetH1Activation(id)) {
    G4cout << " Angle of absorbed photons: mean = " << analysisManager->GetH1(id)->mean()
           << "; rms = " << analysisManager->GetH1(id)->rms() << G4endl;
  }

  G4cout << G4endl;

  if (analysisManager->IsActive()) {
    analysisManager->Write();
    analysisManager->CloseFile();
  }
  if (isMaster) {
    WritePhysicsEndState(analysisManager->GetFileName());
    WriteRandomEngineEndState(analysisManager->GetFileName());
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
